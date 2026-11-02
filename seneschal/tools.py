"""The storyteller's hands. Each tool writes in the notebook or rolls the dice.

A tool never decides whether something is plausible. If a call cannot be carried out, the
message goes back to the model so it can correct itself; the player never sees an error.
"""
import random

from . import dice
from .world import GONE, PLAYER, add_message, advance_time, clock_time, damage_taken, entity, recall, tick_clock, unique_id


class ToolProblem(Exception):
    """Returned to the model as an ordinary tool result."""


def s(description, **extra):
    return {"type": "string", "description": description, **extra}


def i(description, **extra):
    return {"type": "integer", "description": description, **extra}


def b(description):
    return {"type": "boolean", "description": description}


SPECS = {
    "establish_place": ("Make a place real. It can be visited and referred to from now on.", {
        "name": s("Name"), "description": s("What it is like"),
        "exits": {"type": "array", "items": {"type": "string"}, "description": "IDs of connected places"}}, ["name", "description"]),
    "establish_person": ("Make a person real, with a will of their own.", {
        "name": s("Name"), "description": s("What the player would see"), "location": s("Place ID"),
        "personality": s("Temperament"), "motive": s("What they want"), "plan": s("What they are doing about it"),
        "knows": {"type": "array", "items": {"type": "string"}, "description": "What they know"},
        "visible": b("False if they are present but unnoticed")}, ["name", "description", "location"]),
    "establish_item": ("Make an object real. Do this for anything the player gets or relies on.", {
        "name": s("Name"), "description": s("What it is"),
        "at": s("'player', a person ID, a place ID, or an item ID for a container"),
        "visible": b("False if it is present but unnoticed")}, ["name", "description", "at"]),
    "move": ("Move a person or item, or the player ('player'), somewhere. Use 'gone' for destroyed or departed.", {
        "what": s("ID, or 'player'"), "to": s("Destination ID, 'player', or 'gone'")}, ["what", "to"]),
    "update": ("Change a place, person or item. Changes to established canon are logged.", {
        "id": s("Entity ID"), "description": s("New description"), "plan": s("Person's new plan"),
        "attitude": s("Person's attitude to the player"), "learns": s("Something this person now knows"),
        "visible": b("Whether the player can now notice it"),
        "exits": {"type": "array", "items": {"type": "string"}, "description": "A place's new exits"}}, ["id"]),
    "record_fact": ("Write down something now true so it is remembered.", {
        "text": s("The fact"), "public": b("True if the player knows it")}, ["text", "public"]),
    "advance_time": ("Let time pass. Clocks tick and NPCs keep moving.", {
        "minutes": i("Minutes, 1 to 600"), "why": s("What took the time")}, ["minutes", "why"]),
    "create_clock": ("Start a countdown for a threat or deadline.", {
        "name": s("Name"), "segments": i("Size, 2 to 12"), "on_full": s("What happens when it fills"),
        "per_minutes": i("Fill one segment every this many minutes; omit if events alone drive it"),
        "public": b("True if the player is aware of it")}, ["name", "segments", "on_full"]),
    "tick_clock": ("Advance a clock because of what happened.", {
        "id": s("Clock ID"), "by": i("Segments, usually 1"), "why": s("Why")}, ["id", "why"]),
    "reveal_secret": ("Mark a secret as learned by the player. Do this only when it has been earned.", {
        "id": s("Secret ID"), "how": s("How the player learned it")}, ["id", "how"]),
    "open_thread": ("Track a loose end or promise.", {
        "text": s("The thread"), "public": b("True if the player knows about it")}, ["text"]),
    "close_thread": ("Resolve a thread.", {"id": s("Thread ID"), "how": s("How it ended")}, ["id", "how"]),
    "log_drift": ("Record a deliberate departure from the premise, and why the story earned it.", {
        "change": s("How the story has moved"), "why": s("Why that is right for this story")}, ["change", "why"]),
    "set_plan": ("Show the player the checks a plan would need, each with its stakes, before any rolling.", {
        "goal": s("What the plan achieves"),
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "label": s("Step"), "attribute": s("Attribute"), "skill": s("Skill"),
            "difficulty": i("Difficulty"), "stakes": s("What failure costs"),
            "optional": b("True if it is needed only in some cases")},
            "required": ["label", "attribute", "skill", "difficulty", "stakes"]}}}, ["goal", "steps"]),
    "clear_plan": ("Remove the current plan.", {"why": s("Why")}, ["why"]),
    "call_for_roll": ("Roll for one uncertain, consequential attempt. You cannot see the dice until the player accepts them.", {
        "attempt": s("What is being tried"), "attribute": s("Attribute"), "skill": s("Skill"),
        "difficulty": i("1 easy, 2 routine, 3 hard, 4 very hard, 5+ extreme"),
        "stakes": s("What failure costs"), "modifier": i("Bonus or penalty dice"),
        "plan_step": i("Index of the plan step this resolves")}, ["attempt", "attribute", "skill", "difficulty", "stakes"]),
    "rouse_check": ("Roll a Rouse check for a use of the Blood.", {"why": s("Why")}, ["why"]),
    "adjust_track": ("Change Hunger, Health or Willpower.", {
        "track": s("hunger, health or willpower", enum=["hunger", "health", "willpower"]),
        "amount": i("Hunger: change in Hunger. Health or Willpower: damage taken (negative heals)"),
        "kind": s("superficial or aggravated", enum=["superficial", "aggravated"]), "why": s("Why")}, ["track", "amount", "why"]),
    "set_condition": ("Add or remove a condition on the player.", {
        "text": s("The condition"), "active": b("True to add, false to remove")}, ["text", "active"]),
    "say": ("Speak a line in a character's own voice. It appears as that character's message.", {
        "speaker": s("Person ID"), "text": s("Their exact words")}, ["speaker", "text"]),
    "set_scene": ("Change the current scene when the action moves on.", {
        "title": s("Short title"), "description": s("What the player can perceive")}, ["title", "description"]),
    "journal": ("Add a note to the player's journal.", {"text": s("In the character's own terms")}, ["text"]),
    "ask_player": ("Ask for the one detail you need, such as how they intend to do it.", {
        "question": s("The question"), "why": s("Why it matters")}, ["question"]),
    "set_conflict": ("Mark a fight as started, continuing or ended.", {
        "active": b("Whether conflict is under way"), "label": s("What the conflict is"),
        "round": i("Current round")}, ["active"]),
    "set_complication": ("Show the player an unresolved complication; leave the text empty to clear it.", {
        "text": s("The complication")}, ["text"]),
    "end_chronicle": ("End the story.", {"outcome": s("Outcome"), "summary": s("Closing note")}, ["outcome", "summary"]),
    "recall": ("Search all of history: every person, object, fact and line ever said.", {"query": s("Keywords")}, ["query"]),
    "set_pressure": ("Director only: a private note on where the storyteller should apply pressure next.", {
        "text": s("The note")}, ["text"]),
    "update_summary": ("Director only: replace the story-so-far with an updated version.", {
        "text": s("The full summary")}, ["text"]),
}

DIRECTOR_TOOLS = ("establish_place", "establish_person", "establish_item", "move", "update", "record_fact",
                  "create_clock", "tick_clock", "open_thread", "close_thread", "log_drift", "recall",
                  "set_pressure", "update_summary")
STORYTELLER_TOOLS = tuple(name for name in SPECS if name not in ("set_pressure", "update_summary"))


def schemas(names):
    return [{"type": "function", "function": {
        "name": name, "description": SPECS[name][0],
        "parameters": {"type": "object", "properties": SPECS[name][1], "required": SPECS[name][2]}}}
        for name in names]


class Toolbox:
    def __init__(self, state, by="storyteller", rng=random, channel="storyteller"):
        self.state, self.by, self.rng, self.channel = state, by, rng, channel

    def run(self, name, args):
        allowed = STORYTELLER_TOOLS if self.by == "storyteller" else DIRECTOR_TOOLS
        if name not in allowed:
            return f"Problem: no tool named {name}. Available: {', '.join(allowed)}."
        try:
            result = getattr(self, "tool_" + name)(**self._clean(name, args))
        except ToolProblem as problem:
            result = f"Problem: {problem}"
        except (TypeError, KeyError, ValueError, AttributeError) as problem:
            result = f"Problem: bad arguments for {name}: {problem!r}"
        self.state["log"].append({"turn": self.state["turn"], "by": self.by, "tool": name, "args": args,
                                  "result": str(result)[:500]})
        del self.state["log"][:-300]
        return result

    @staticmethod
    def _clean(name, args):
        known = SPECS[name][1]
        return {k: v for k, v in (args or {}).items() if k in known}

    # Establishing things -------------------------------------------------------------

    def tool_establish_place(self, name, description, exits=()):
        state = self.state
        place_id = unique_id(state["places"], name)
        state["places"][place_id] = {"name": name, "description": description, "exits": [e for e in exits if e in state["places"]]}
        for exit_id in state["places"][place_id]["exits"]:
            state["places"][exit_id].setdefault("exits", []).append(place_id)
        here = state["player"]["location"]
        return (f"Place '{place_id}' established. To let the player go there use move or link it to '{here}' "
                f"with update(id='{place_id}', exits=[...]).")

    def tool_establish_person(self, name, description, location, personality="", motive="", plan="", knows=(), visible=True):
        state = self.state
        person_id = unique_id(state["people"], name)
        state["people"][person_id] = {
            "name": name, "description": description, "location": self._place(location), "personality": personality,
            "motive": motive, "plan": plan, "voice": "", "attitude": "", "knows": list(knows), "visible": visible}
        return f"Person '{person_id}' established."

    def tool_establish_item(self, name, description, at, visible=True):
        state = self.state
        item_id = unique_id(state["items"], name)
        state["items"][item_id] = {"name": name, "description": description, "at": self._holder(at), "visible": visible, "tags": []}
        return f"Item '{item_id}' established at {state['items'][item_id]['at']}."

    def _place(self, value):
        if value == GONE or value in self.state["places"]:
            return value
        raise ToolProblem(f"unknown place '{value}'. Known: {', '.join(self.state['places'])}. Establish it first.")

    def _holder(self, value):
        state = self.state
        if value in (PLAYER, GONE) or value in state["places"] or value in state["people"] or value in state["items"]:
            return value
        raise ToolProblem(f"unknown holder '{value}'. Use 'player', a place ID, a person ID or an item ID.")

    def tool_move(self, what, to):
        state = self.state
        if what == PLAYER:
            origin = state["player"]["location"]
            state["player"]["location"] = self._place(to)
            state["dialogue_target"] = None
            place = state["places"].get(to)
            if place:
                for start, end in ((origin, to), (to, origin)):
                    exits = state["places"].get(start, {}).setdefault("exits", [])
                    if end not in exits and end in state["places"] and start != end:
                        exits.append(end)
                state["scene"] = {"title": place["name"], "description": place["description"]}
            return f"The player is now at {to}."
        kind, record = entity(state, what)
        if kind == "person":
            record["location"] = self._place(to)
            if state["dialogue_target"] == what and to != state["player"]["location"]:
                state["dialogue_target"] = None
        elif kind == "item":
            record["at"] = self._holder(to)
        else:
            raise ToolProblem(f"nothing movable called '{what}'.")
        return f"{what} is now at {to}."

    def tool_update(self, id, description=None, plan=None, attitude=None, learns=None, visible=None, exits=None):
        state = self.state
        kind, record = entity(state, id)
        if not kind:
            raise ToolProblem(f"nothing called '{id}'. Use recall to look for it.")
        if description is not None:
            if record.get("description") and record["description"] != description:
                state["log"].append({"turn": state["turn"], "by": self.by, "tool": "canon_changed", "args": {"id": id},
                                     "result": f"was: {record['description'][:300]}"})
            record["description"] = description
        for field, value in (("plan", plan), ("attitude", attitude)):
            if value is not None:
                record[field] = value
        if learns:
            record.setdefault("knows", []).append(learns)
        if visible is not None:
            record["visible"] = visible
        if exits is not None:
            if kind != "place":
                raise ToolProblem("only places have exits.")
            record["exits"] = [self._place(e) for e in exits]
            for exit_id in record["exits"]:
                other = state["places"][exit_id].setdefault("exits", [])
                if id not in other:
                    other.append(id)
        return f"{id} updated."

    def tool_record_fact(self, text, public):
        state = self.state
        state["facts"].append({"text": text, "public": bool(public), "minute": state["minute"], "turn": state["turn"]})
        return "Recorded."

    # Time, clocks, secrets, threads --------------------------------------------------

    def tool_advance_time(self, minutes, why):
        if type(minutes) is not int or not 1 <= minutes <= 600:
            raise ToolProblem("minutes must be between 1 and 600.")
        self.state["advanced"] = True
        advance_time(self.state, minutes)
        return f"It is now {clock_time(self.state)}." + self._alerts()

    def _alerts(self):
        return (" ALERTS: " + " ".join(self.state["alerts"])) if self.state["alerts"] else ""

    def tool_create_clock(self, name, segments, on_full, per_minutes=None, public=False):
        state = self.state
        clock_id = unique_id(state["clocks"], name)
        state["clocks"][clock_id] = {"name": name, "segments": max(2, min(12, segments)), "filled": 0,
                                     "per_minutes": per_minutes, "public": bool(public), "fired": False,
                                     "on_full": on_full, "last_tick": state["minute"]}
        return f"Clock '{clock_id}' started."

    def tool_tick_clock(self, id, why, by=1):
        if id not in self.state["clocks"]:
            raise ToolProblem(f"no clock '{id}'. Known: {', '.join(self.state['clocks'])}.")
        tick_clock(self.state, id, by, why)
        clock = self.state["clocks"][id]
        return f"{clock['name']}: {clock['filled']}/{clock['segments']}." + self._alerts()

    def tool_reveal_secret(self, id, how):
        secret = self.state["secrets"].get(id)
        if not secret:
            raise ToolProblem(f"no secret '{id}'. Secrets are fixed: new ones cannot be added here, use record_fact.")
        secret.update(revealed=True, how=how)
        return f"Secret '{id}' is now known to the player. Its truth is unchanged: {secret['truth']}"

    def tool_open_thread(self, text, public=False):
        thread_id = unique_id(self.state["threads"], text[:30])
        self.state["threads"][thread_id] = {"text": text, "open": True, "public": bool(public)}
        return f"Thread '{thread_id}' opened."

    def tool_close_thread(self, id, how):
        if id not in self.state["threads"]:
            raise ToolProblem(f"no thread '{id}'.")
        self.state["threads"][id].update(open=False, how=how)
        return "Closed."

    def tool_log_drift(self, change, why):
        state = self.state
        state["drift"].append({"minute": state["minute"], "turn": state["turn"], "change": change, "why": why, "by": self.by})
        return "Logged."

    # Dice ----------------------------------------------------------------------------

    def _trait(self, kind, value):
        name = str(value).strip().lower()
        known = dice.ATTRIBUTES if kind == "attribute" else dice.SKILLS
        if name not in known:
            raise ToolProblem(f"'{value}' is not a V5 {kind}. Choose from: {', '.join(known)}.")
        return name

    def tool_set_plan(self, goal, steps):
        if not steps:
            raise ToolProblem("a plan needs at least one step.")
        clean = []
        for step in steps:
            clean.append({"label": step["label"], "attribute": self._trait("attribute", step["attribute"]),
                          "skill": self._trait("skill", step["skill"]), "difficulty": int(step["difficulty"]),
                          "stakes": step["stakes"], "optional": bool(step.get("optional")), "status": "open"})
        self.state["plan"] = {"goal": goal, "steps": clean}
        return "Plan shown to the player. Let them confirm or change it before you roll."

    def tool_clear_plan(self, why):
        self.state["plan"] = None
        return "Plan cleared."

    def tool_call_for_roll(self, attempt, attribute, skill, difficulty, stakes, modifier=0, plan_step=None):
        state = self.state
        if state["pending_roll"]:
            raise ToolProblem("a roll is already waiting for the player. Stop and let them answer it.")
        attribute, skill = self._trait("attribute", attribute), self._trait("skill", skill)
        difficulty = max(1, min(9, int(difficulty)))
        player = state["player"]
        pool = player["attributes"].get(attribute, 0) + player["skills"].get(skill, 0) + int(modifier or 0)
        result = dice.roll(pool, player["hunger"], difficulty, self.rng)
        state["pending_roll"] = {"attempt": attempt, "attribute": attribute, "skill": skill, "difficulty": difficulty,
                                 "stakes": stakes, "pool": max(1, pool), "plan_step": plan_step, "rerolled": False, **result}
        return ("The dice are rolled but hidden from you. Do not narrate the outcome and do not call another roll. "
                "Write one or two sentences that set the moment. You will get the result after the player accepts it.")

    def tool_rouse_check(self, why):
        state = self.state
        result = dice.rouse(self.rng)
        player = state["player"]
        if not result["passed"]:
            player["hunger"] = min(5, player["hunger"] + 1)
        add_message(state, "event", f"Rouse check for {why}: {result['die']}. "
                    + ("Hunger holds." if result["passed"] else f"Hunger rises to {player['hunger']}."), label="Rouse")
        return f"Rolled {result['die']}. Hunger is now {player['hunger']}."

    def tool_adjust_track(self, track, amount, why, kind="superficial"):
        player = self.state["player"]
        if track == "hunger":
            player["hunger"] = max(0, min(5, player["hunger"] + int(amount)))
            return f"Hunger is {player['hunger']}."
        if track not in ("health", "willpower"):
            raise ToolProblem("track must be hunger, health or willpower.")
        record = player[track]
        record[kind] = max(0, record[kind] + int(amount))
        overflow = damage_taken(record) - record["max"]
        if overflow > 0:  # V5: superficial damage past the track's end becomes aggravated.
            record["superficial"] = max(0, record["superficial"] - overflow)
            record["aggravated"] = min(record["max"], record["aggravated"] + overflow)
        remaining = record["max"] - damage_taken(record)
        note = " The track is full: the character is impaired or torpid." if remaining <= 0 else ""
        return f"{track.title()} has {remaining} of {record['max']} left.{note}"

    # Presenting the story ------------------------------------------------------------

    def tool_set_condition(self, text, active):
        conditions = self.state["player"]["conditions"]
        if active and text not in conditions:
            conditions.append(text)
        elif not active and text in conditions:
            conditions.remove(text)
        return "Updated."

    def tool_say(self, speaker, text):
        person = self.state["people"].get(speaker)
        if not person:
            raise ToolProblem(f"no person '{speaker}'. Establish them first.")
        add_message(self.state, "npc_message", text, speaker=person["name"], channel=self.channel)
        return "Said."

    def tool_set_scene(self, title, description):
        self.state["scene"] = {"title": title, "description": description}
        return "Scene set."

    def tool_journal(self, text):
        self.state["journal"].append(text)
        return "Noted."

    def tool_ask_player(self, question, why=""):
        self.state["clarification"] = {"question": question, "why": why, "original": self.state.get("last_input") or ""}
        return "The question will be shown. Say it naturally in your reply as well, and wait for the answer."

    def tool_set_conflict(self, active, label="", round=1):
        self.state["conflict"] = {"label": label or "Conflict", "round": round} if active else None
        return "Conflict updated."

    def tool_set_complication(self, text):
        self.state["complication"] = text or None
        return "Complication updated."

    def tool_end_chronicle(self, outcome, summary):
        self.state["ending"] = {"outcome": outcome, "summary": summary}
        return "The chronicle is closed. Write the final moment."

    def tool_recall(self, query):
        found = recall(self.state, query)
        return "\n".join(found) if found else "Nothing found."

    # Director ------------------------------------------------------------------------

    def tool_set_pressure(self, text):
        self.state["pressure"] = text
        return "Noted for the storyteller."

    def tool_update_summary(self, text):
        self.state["summary"] = text
        self.state["summary_upto"] = self.state["seq"]
        return "Summary updated."
