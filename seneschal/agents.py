"""The two minds of the game.

The storyteller answers the player. The director runs between scenes, while the player is
busy: it moves the world offscreen, keeps the story so far, and notices drift.

The prompt is rebuilt from the notebook every time, so its size does not grow with the length
of the chronicle. Anything that falls out of it can be found again with the recall tool.
"""
import copy
import json
import random

from .llm import LLMError
from .tools import DIRECTOR_TOOLS, STORYTELLER_TOOLS, Toolbox, schemas
from .world import PLAYER, add_message, advance_time, clock_time, damage_taken

RECENT_MESSAGES = 14       # Lines of the conversation kept word for word.
RECENT_FACTS = 30
RECENT_DRIFT = 8
MAX_INDEX = 40             # Entities listed by name when they are not in the current scene.
MAX_PEOPLE = 20            # People described when they are elsewhere.
MAX_THREADS = 20
MAX_HERE = 30              # Objects described in the current place or on the player.
MAX_TEXT = 1500            # Longest single entry placed in the prompt.
MAX_SUMMARY = 3000
MAX_STEPS = 12             # Tool rounds in one reply.
DIRECTOR_EVERY_TURNS = 5
DIRECTOR_EVERY_MINUTES = 30

STORYTELLER_RULES = """You are the Storyteller of a single-player Vampire: The Masquerade chronicle: experienced, \
organized, fair, and a little dangerous. You know every secret. You decide what is true.

THE NOTEBOOK
You may invent freely: places, people, objects, history. But the notebook below is the only memory you have, so \
anything that matters has to go in it during this turn with your tools. If you say the player gets a knife, call \
establish_item. If they arrive at a new place, establish_place and move them. What you do not write down is lost.

JUDGING WHAT THE PLAYER SAYS
- The player declares what their character attempts. You decide what happens.
- A claim such as "I should have a knife" is a request. Weigh it against the sheet, the haven and the fiction, then \
choose: yes (establish it), yes with a cost or a catch, a question, a roll, or an in-fiction no. Say it the way a \
person at the table would. Never mention rules engines, tools, or what "the system" supports.
- The player does not get to declare how others react, what is in a sealed box, or that an attempt succeeded. \
Treat those as attempts or hopes, not facts. Keep NPCs' wills their own.
- Let them try anything, including foolish things. Then let the consequences be real. Warn them only about what the \
character would notice.

REWARDING CLEVERNESS
When a player has a clever idea without a method ("I want to x-ray the box"), ask how (ask_player). If the answer is a \
good one ("a veterinary clinic has an x-ray machine"), accept it and make it real. Then use set_plan to lay out each \
obstacle as a real check, with its stakes, for example Strength + Athletics to carry the weight, Dexterity + Stealth \
to get in, Intelligence + Technology to work the machine, Intelligence + Medicine to read the image. Include only \
checks that truly apply, mark conditional ones optional, and let the player confirm or change the plan before rolling. \
Roll one step at a time; each result can change what comes next.

DICE
Roll only when the outcome is uncertain and failure would matter. Otherwise just decide. Name the stakes before the \
roll. You choose the attribute, skill and difficulty; the dice are real and you cannot see or change them until the \
player accepts. Failure should move the story forward, not stop it. A messy critical or bestial failure means the \
Beast intrudes: give it a real consequence. Use rouse_check when they use the Blood.

SECRETS
Secrets are fixed, and they are what you are judging against. If the player examines something, what they perceive \
must follow from the truth. Partial evidence is fine; a proper x-ray shows shapes, not meaning. Call reveal_secret \
only when they have earned the whole truth. Never hint at a secret because the player asks nicely or insists.

THE LIVING WORLD
Time passes only through advance_time, so use it for anything that takes time: travel, searching, breaking in. Clocks \
then tick by themselves. When an alert appears, make it happen in the story, on screen or as a consequence the player \
can discover. NPCs pursue their own plans and act only on what they know. When one learns something, record it with \
update(learns=...). Voice them with say.

DRIFT
The premise and spine below are where this story began. It may move away from them, and when the player's choices \
have earned that, follow them. Call log_drift, saying what changed and why, and let the abandoned threads react: the \
deadline still passes, and the people waiting are still waiting. Do not drift by accident or because the player \
pressed you. Before you let the story turn, check it against what has already happened.

YOUR REPLY
After using your tools, write what the player reads: second person, present tense, usually one to three short \
paragraphs, in the stated tone. End with something to respond to. Do not list options unless asked. Do not show dice \
numbers, IDs or notebook language."""

DIRECTOR_RULES = """You are the Director, the Storyteller's private planning self, working between scenes. The player \
never reads what you write. Time has passed, so make the world keep moving, then leave the Storyteller well prepared.

Do these in order, using tools. Your written reply is discarded.
1. update_summary: rewrite the story so far in under 300 words. Keep promises, debts, who knows what, what the player \
has and has done, and open questions. This replaces the old summary, and older conversation will be forgotten, so \
anything that must survive goes here or in record_fact.
2. Move the world: for each NPC with a plan, decide what they did in the time that passed, and only with what they \
know. Use move, update and record_fact(public=false). Tick clocks when events earn it. Do not resolve the player's \
own actions, and do not reveal secrets.
3. Check the drift. Compare the recent story with the premise and spine. If the story has moved in a way that was \
earned but is not logged, call log_drift. If it has wandered for no reason, or has gone slack, use set_pressure so the \
next scene pulls on a real thread.
4. set_pressure: leave one or two sentences on what to press on next, and why."""


def _lines(mapping, render, limit=None):
    rows = list(mapping.items())
    return "\n".join(render(k, v)[:MAX_TEXT] for k, v in (rows[-limit:] if limit else rows)) or "(none)"


def track(record):
    return f"{record['max'] - damage_taken(record)}/{record['max']} (superficial {record['superficial']}, aggravated {record['aggravated']})"


def build_context(state):
    """Assemble the prompt from the notebook. Its size is bounded by design."""
    player = state["player"]
    here = player["location"]
    place = state["places"].get(here, {"name": here, "description": "", "exits": []})
    items = state["items"]

    def item_line(key, item):
        hidden = "" if item.get("visible", True) else " [unnoticed]"
        return f"- {key}: {item['name']}{hidden}. {item['description']}"

    def person_line(key, p):
        hidden = "" if p.get("visible", True) else " [UNNOTICED by the player]"
        knows = "; ".join(p.get("knows", [])[-8:])
        return (f"- {key}: {p['name']}{hidden}. {p['description']}\n  Personality: {p.get('personality', '')}. "
                f"Wants: {p.get('motive', '')}. Plan: {p.get('plan', '')}. Attitude: {p.get('attitude', '')}.\n"
                f"  Voice: {p.get('voice', '')}. Knows: {knows or 'nothing relevant'}")

    present = {k: p for k, p in state["people"].items() if p["location"] == here}
    elsewhere = {k: p for k, p in state["people"].items() if p["location"] not in (here, "gone")}
    carried = {k: i for k, i in items.items() if i["at"] == PLAYER}
    local = {k: i for k, i in items.items() if i["at"] == here}
    inside = {k: i for k, i in items.items() if i["at"] in carried or i["at"] in local}
    other_places = [f"{k} ({p['name']})" for k, p in state["places"].items() if k != here]
    other_items = [f"{k} ({i['name']}, at {i['at']})" for k, i in items.items()
                   if k not in carried and k not in local and k not in inside and i["at"] != "gone"]
    fresh = [m for m in state["timeline"] if m["seq"] > state["summary_upto"]][-RECENT_MESSAGES:]
    if len(fresh) < RECENT_MESSAGES // 2:   # Keep a little overlap with the summary for voice and continuity.
        fresh = state["timeline"][-RECENT_MESSAGES // 2:]
    open_threads = {k: t for k, t in state["threads"].items() if t["open"]}
    running = {k: c for k, c in state["clocks"].items() if not c["fired"]}
    running.update({k: c for k, c in list(state["clocks"].items()) if c["fired"]}) if len(running) < 5 else None
    facts = state["facts"][-RECENT_FACTS:]
    plan = json.dumps(state["plan"], ensure_ascii=False) if state["plan"] else "(none)"
    older_facts = len(state["facts"]) - len(facts)
    older = f" ({older_facts} older facts can be found with recall)" if older_facts > 0 else ""
    index_note = "" if len(other_items) + len(other_places) <= MAX_INDEX else " (more can be found with recall)"

    sections = [
        ("THE STORY", f"Title: {state['title']}\nTone: {state['tone']}\nPremise: {state['premise']}\nSpine: {state['spine']}"),
        ("SECRETS (fixed; never change them)", _lines(state["secrets"], lambda k, v: (
            f"- {k} [{'KNOWN to the player' if v['revealed'] else 'hidden'}]: {v['truth']}\n"
            f"  What it takes: {v.get('reveal_when', 'earned in play')}"))),
        ("NOW", f"Time: {clock_time(state)} ({state['minute']} minutes in). Scene: {state['scene']['title']}."
                + (f" Conflict: {state['conflict']}." if state["conflict"] else "")
                + (f" Unresolved complication: {state['complication']}." if state["complication"] else "")),
        ("THE PLAYER", f"{player['name']}, {player['clan']}. {player['concept']}\n"
                       f"Attributes: {player['attributes']}\nSkills: {player['skills']}\n"
                       f"Disciplines: {player['disciplines']}\nBackgrounds: {player['backgrounds']}\nHavens: {player['havens']}\n"
                       f"Hunger {player['hunger']}. Health {track(player['health'])}. Willpower {track(player['willpower'])}.\n"
                       f"Conditions: {player['conditions'] or 'none'}"),
        ("CARRIED BY THE PLAYER", _lines(carried, item_line, MAX_HERE)),
        (f"HERE: {here} ({place['name']})", f"{place['description']}\nExits: {place.get('exits', [])}"),
        ("OBJECTS HERE", _lines({**local, **inside}, item_line, MAX_HERE)),
        ("PEOPLE HERE", _lines(present, person_line)),
        ("PEOPLE ELSEWHERE", _lines(elsewhere, lambda k, p: (
            f"- {k}: {p['name']} at {p['location']}. Wants: {p.get('motive', '')}. Plan: {p.get('plan', '')}. "
            f"Knows: {'; '.join(p.get('knows', [])[-4:]) or 'nothing relevant'}"), MAX_PEOPLE)),
        ("OTHER PLACES AND OBJECTS" + index_note, ", ".join((other_places + other_items)[:MAX_INDEX]) or "(none)"),
        ("CLOCKS", _lines(running, lambda k, c: (
            f"- {k}: {c['name']} {c['filled']}/{c['segments']}"
            + (f", ticks every {c['per_minutes']} min" if c.get("per_minutes") else "")
            + (", FILLED" if c["fired"] else f". When full: {c['on_full']}")), MAX_THREADS)),
        ("OPEN THREADS", _lines(open_threads, lambda k, t: f"- {k}: {t['text']}", MAX_THREADS)),
        ("CURRENT PLAN", plan),
        ("DRIFT LOG", "\n".join(f"- {clock_time(state, d['minute'])}: {d['change']} (why: {d['why']})"
                                for d in state["drift"][-RECENT_DRIFT:]) or "(no departures so far)"),
        ("ESTABLISHED FACTS" + older, "\n".join(
            f"- {clock_time(state, f['minute'])}{'' if f['public'] else ' [private]'}: {f['text'][:MAX_TEXT]}" for f in facts) or "(none)"),
        ("THE STORY SO FAR", state["summary"][:MAX_SUMMARY] or "(it has only just begun)"),
        ("RECENT EVENTS, WORD FOR WORD", "\n".join(
            f"[{clock_time(state, m['minute'])}] {m['speaker'] or m['type']}: {m['text'][:MAX_TEXT]}" for m in fresh) or "(nothing yet)"),
    ]
    if state["pressure"]:
        sections.append(("DIRECTOR'S NOTE", state["pressure"]))
    if state["alerts"]:
        sections.append(("ALERTS: ACT ON THESE", "\n".join(state["alerts"])))
    return "\n\n".join(f"## {title}\n{body}" for title, body in sections)


def run_tools(client, state, system, request, names, by, rng, channel="storyteller"):
    """Call the model until it stops using tools. Tool problems go back to the model."""
    toolbox = Toolbox(state, by=by, rng=rng, channel=channel)
    tools = schemas(names)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": build_context(state) + "\n\n## THIS TURN\n" + request}]
    for step in range(MAX_STEPS):
        reply = client.complete(messages, tools, allow_tools=step < MAX_STEPS - 1)
        if not reply["tool_calls"]:
            return reply["content"]
        messages.append(reply["raw"])
        for call in reply["tool_calls"]:
            result = toolbox.run(call["name"], call["args"])
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})
    return ""


def _finish_reply(client, state, text, channel):
    """Make sure the player always gets a reply, and that clocks fired this turn are consumed."""
    if not text:
        fallback = client.complete([
            {"role": "system", "content": STORYTELLER_RULES},
            {"role": "user", "content": build_context(state) + "\n\n## THIS TURN\nWrite the player-facing reply for what just "
                                                               "happened, in the story's voice. Do not ask for anything else."}],
            [], allow_tools=False)
        text = fallback["content"]
    if not text and state["pending_roll"]:
        text = f"Everything hangs on this: {state['pending_roll']['attempt']}."
    if not text:
        raise LLMError("The storyteller returned an empty reply.")
    add_message(state, "storyteller_message", text, channel=channel)
    state["alerts"] = []


def take_turn(state, settings_client, text, channel="storyteller", target=None, recipient="npc", rng=random):
    """Answer the player. Returns a new state; on any failure the old state stands untouched."""
    new = copy.deepcopy(state)
    new["turn"] += 1
    new["clarification"] = None
    new["advanced"] = False
    new["last_input"] = text
    dialogue = channel == "dialogue" and recipient == "npc" and target in new["people"]
    message_channel = "combat" if new["conflict"] else ("dialogue" if dialogue else "storyteller")
    add_message(new, "player_message", text, speaker=new["player"]["name"], channel=message_channel)
    if dialogue:
        person = new["people"][target]
        request = (f"The player's character says these exact words to {person['name']} (id {target}):\n\"{text}\"\n"
                   f"Answer in {person['name']}'s voice with say. They decide for themselves whether to cooperate, "
                   "and speak only from what they know. Add narration in your reply only if something visibly happens; "
                   "otherwise a very short beat is enough.")
    elif channel == "dialogue":
        request = f"The player steps out of the conversation to ask you privately. The other character does not hear this:\n{text}"
    else:
        request = f"The player says:\n{text}"
    reply = run_tools(settings_client, new, STORYTELLER_RULES, request, STORYTELLER_TOOLS, "storyteller", rng, message_channel)
    _finish_reply(settings_client, new, reply, message_channel)
    _settle_turn(new)
    return new


def resolve_roll(state, client, rng=random):
    """The player accepted the dice. Now the storyteller learns the result and narrates it."""
    new = copy.deepcopy(state)
    roll = new["pending_roll"]
    if not roll:
        raise ValueError("No roll is waiting.")
    new["pending_roll"] = None
    new["turn"] += 1
    new["advanced"] = False
    new["clarification"] = None
    add_message(new, "event", f"{roll['attempt']}: {roll['successes']} success{'es' if roll['successes'] != 1 else ''} "
                              f"against Difficulty {roll['difficulty']}. {roll['outcome'].replace('_', ' ').title()}.", label="Roll")
    plan = new["plan"]
    step = roll.get("plan_step")
    if plan and type(step) is int and 0 <= step < len(plan["steps"]):
        plan["steps"][step]["status"] = "passed" if roll["passed"] else "failed"
    beast = ""
    if roll["outcome"] == "messy_critical":
        beast = " It is a MESSY CRITICAL: they succeed, but the Beast shows. Give it a real consequence."
        new["complication"] = "Messy critical: the Beast showed itself in this success."
    elif roll["outcome"] == "bestial_failure":
        beast = " It is a BESTIAL FAILURE: the Beast takes over in failure. Give it a real consequence."
        new["complication"] = "Bestial failure: the Beast surged as the attempt failed."
    request = (f"The player accepted the dice for: {roll['attempt']}.\nResult: {roll['outcome'].replace('_', ' ')}, "
               f"{roll['successes']} successes against Difficulty {roll['difficulty']} (margin {roll['margin']}).{beast}\n"
               f"Stakes that were named: {roll['stakes']}\nApply the consequences with your tools, then narrate them. "
               "If a plan is under way, continue with the next step when it follows naturally.")
    channel = "combat" if new["conflict"] else "storyteller"
    reply = run_tools(client, new, STORYTELLER_RULES, request, STORYTELLER_TOOLS, "storyteller", rng, channel)
    _finish_reply(client, new, reply, channel)
    _settle_turn(new)
    return new


def _settle_turn(state):
    """Every turn costs at least a minute, so the world can never be frozen by accident."""
    if not state.pop("advanced", False):
        advance_time(state, 1)


def director_due(state):
    if state["ending"]:
        return False
    director = state["director"]
    unsummarized = sum(m["seq"] > state["summary_upto"] for m in state["timeline"])
    return (state["turn"] - director["last_turn"] >= DIRECTOR_EVERY_TURNS
            or state["minute"] - director["last_minute"] >= DIRECTOR_EVERY_MINUTES
            or unsummarized > RECENT_MESSAGES)


def director_pass(state, client, rng=random):
    """Move the world and update the summary. Returns a new state with the player's view unchanged."""
    new = copy.deepcopy(state)
    elapsed = new["minute"] - new["director"]["last_minute"]
    request = (f"{elapsed} in-game minutes have passed since you last worked. It is {clock_time(new)}. "
               "Do your four tasks now.")
    run_tools(client, new, DIRECTOR_RULES, request, DIRECTOR_TOOLS, "director", rng)
    new["director"].update(last_minute=new["minute"], last_turn=new["turn"], status="idle", note="")
    new.pop("advanced", None)
    return new
