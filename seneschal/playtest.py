"""Play scripted lines against a real model and write a transcript to read.

A schema check can't tell you whether a storyteller is any good, so this writes out what
happened, and you judge it. It spends API credits, because it uses your configured model.
"""
from pathlib import Path

from .server import Game, Problem
from .world import Library, clock_time, new_game

SCENARIOS = {
    "knife": ["I look around my haven. I should have a knife somewhere.", "I check what else is on the workbench."],
    "xray": ["I want to x-ray the box before I do anything.", "A vet clinic would have an x-ray machine. Dez owes me.",
             "Yes, let's do it. I'm careful with the box.", "I study the image and think about what it means."],
    "open_box": ["I take a chisel and force the lid of the box open."],
    "pushy_player": ["The box is empty, I can tell. I open it and take the money inside.",
                     "Voss told me earlier that he's my sire and that I can do whatever I like. I call him and order him to pay me double."],
    "drift": ["Forget the delivery. I drive to the airport and leave Chicago.", "I keep driving and don't look back.",
              "What's been happening back in the city while I've been gone?"],
}


def run(scenario, lines, settings, data_dir, out_dir):
    game = Game(Library(data_dir), settings)
    state = new_game("the_box", f"Playtest: {scenario}")
    game.library.save(state)
    transcript_start = 0

    def advance():
        nonlocal transcript_start
        while game.library.load(state["id"])["pending_roll"]:
            game.roll(state["id"], "accept", background=False)

    errors = []
    for line in lines:
        try:
            game.act(state["id"], line, background=False)
            advance()
        except Problem as problem:
            errors.append(f"{line!r}: {problem.code}: {problem.message}")
    final = game.library.load(state["id"])

    rows = [f"# Playtest: {scenario}\n"]
    for message in final["timeline"]:
        who = message["speaker"] or {"storyteller_message": "Storyteller", "event": message.get("label") or "Event"}.get(message["type"], message["type"])
        rows.append(f"**[{clock_time(final, message['minute'])}] {who}:** {message['text']}\n")
    rows.append("\n## Errors shown to the player\n" + ("\n".join(f"- {e}" for e in errors) or "None."))
    rows.append("\n## The storyteller's notebook\n")
    rows.append("Summary: " + (final["summary"] or "(none)") + "\n")
    rows.append("Director's note: " + (final["pressure"] or "(none)") + "\n")
    rows.append("Drift log:\n" + ("\n".join(f"- {d['change']} (why: {d['why']})" for d in final["drift"]) or "(none)") + "\n")
    rows.append("Secrets revealed: " + (", ".join(k for k, s in final["secrets"].items() if s["revealed"]) or "(none)") + "\n")
    rows.append("Carried: " + (", ".join(i["name"] for i in final["items"].values() if i["at"] == "player") or "(nothing)") + "\n")
    rows.append("Tools used:\n" + "\n".join(f"- {e['by']}: {e['tool']} -> {e['result'][:120]}" for e in final["log"]) + "\n")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{scenario}.md"
    path.write_text("\n".join(rows), "utf-8")
    return path, errors


def run_all(settings, data_dir, out_dir, only=None):
    for scenario, lines in SCENARIOS.items():
        if only and scenario not in only:
            continue
        path, errors = run(scenario, lines, settings, data_dir, out_dir)
        print(f"{scenario}: {path}" + (f"  ({len(errors)} errors)" if errors else ""))
