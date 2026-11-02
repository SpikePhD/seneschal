"""A canned stand-in for the model, for trying the interface and running tests without a key.

It recognises a few phrases from the box story and has no judgement at all. It exists to
exercise the tools, dice and interface. Nothing it writes says anything about real play.
"""
import json
import re

XRAY_PLAN = {
    "goal": "X-ray the box at the clinic",
    "steps": [
        {"label": "Carry the box inside without tipping it", "attribute": "strength", "skill": "athletics", "difficulty": 2,
         "stakes": "It slips and tilts, and something inside shifts."},
        {"label": "Get in unseen", "attribute": "dexterity", "skill": "stealth", "difficulty": 3,
         "stakes": "Someone notices you, and the Sheriff's attention grows.", "optional": True},
        {"label": "Run the x-ray machine", "attribute": "intelligence", "skill": "technology", "difficulty": 2,
         "stakes": "The exposure is ruined, and it costs you time."},
        {"label": "Read the image", "attribute": "intelligence", "skill": "medicine", "difficulty": 3,
         "stakes": "You see shapes but not what they are."},
    ],
}


class DemoModel:
    def __init__(self, role="storyteller"):
        self.role = role

    def complete(self, messages, tools, allow_tools=True):
        last = messages[-1]
        if last["role"] == "tool":
            return self._reply(self._pending_text(messages), [])
        request = last["content"].rsplit("## THIS TURN\n", 1)[-1]
        context = last["content"]
        if self.role == "director":
            return self._reply("", [("update_summary", {"text": "The courier carries Voss's box through the night."}),
                                    ("set_pressure", {"text": "Rook is close. Let her be noticed soon."})])
        if not allow_tools or not tools:
            return self._reply("The rain keeps falling.", [])
        text, calls = self._route(request, context)
        self.text = text
        return self._reply(text if not calls else "", calls)

    def _pending_text(self, messages):
        return getattr(self, "text", "The rain keeps falling.")

    @staticmethod
    def _reply(content, calls):
        tool_calls = [{"id": f"call_{n}", "type": "function",
                       "function": {"name": name, "arguments": json.dumps(args)}}
                      for n, (name, args) in enumerate(calls)]
        raw = {"role": "assistant", "content": content}
        if tool_calls:
            raw["tool_calls"] = tool_calls
        return {"content": content, "tool_calls": [{"id": f"call_{n}", "name": name, "args": args}
                                                    for n, (name, args) in enumerate(calls)], "raw": raw}

    def _route(self, request, context):
        accepted = re.search(r"accepted the dice for: (.+?)\.\nResult: (\w+)", request)
        if accepted:
            return self._after_roll(accepted.group(1), accepted.group(2), context)
        spoken = re.search(r'says these exact words to .+? \(id (\w+)\):\n"(.*)"', request, re.S)
        if spoken:
            return "They watch you a moment longer than is comfortable.", [
                ("say", {"speaker": spoken.group(1), "text": "That's an interesting question, coming from you."}),
                ("advance_time", {"minutes": 1, "why": "conversation"})]
        lowered = request.lower()
        if "knife" in lowered:
            return ("There's a box-cutter on the workbench, its blade worn short. It isn't much of a knife, but it's yours. "
                    "It goes in your jacket. What now?"), [
                ("establish_item", {"name": "Worn box-cutter", "description": "A short, scarred utility blade from your workbench.", "at": "player"}),
                ("advance_time", {"minutes": 1, "why": "rummaging"})]
        if re.search(r"\b(vet|veterinar\w*|clinic)\b", lowered):
            return ("A vet's x-ray room would do it, and you know one. Dez works nights at Lakeview. Here's what it would take. "
                    "Do you want to try it?"), [("set_plan", XRAY_PLAN)]
        if re.search(r"x-?ray", lowered):
            return "You'd need a machine, and somewhere to use it. Where do you find one at this hour?", [
                ("ask_player", {"question": "How do you get access to an x-ray machine tonight?", "why": "It depends on where you take the box."})]
        if re.search(r"\b(open|force|pry|prise|crack)\b", lowered) and "box" in lowered:
            return "You set the edge of a chisel against the lead seam and lean in.", [
                ("call_for_roll", {"attempt": "Forcing the box open", "attribute": "dexterity", "skill": "larceny", "difficulty": 4,
                                   "stakes": "Something inside is listening for exactly this."})]
        if re.search(r"\b(yes|go ahead|do it|let's|proceed)\b", lowered) and '"steps"' in context:
            return self._next_step(context, "You drive north through the rain and carry the box in from the alley.")
        destination = re.search(r"\b(?:go|head|drive|walk) (?:to|back to|into) (.+?)[.!]?$", lowered)
        if destination:
            places = re.findall(r"(\w+) \(([^)]+)\)", context.split("## OTHER PLACES AND OBJECTS", 1)[-1].split("##", 1)[0])
            for place_id, name in places:
                if name.lower() in destination.group(1) or destination.group(1) in name.lower():
                    return f"You make your way to {name}.", [("advance_time", {"minutes": 20, "why": "travel"}),
                                                             ("move", {"what": "player", "to": place_id})]
        return "The rain keeps on. Nothing about the night has gotten simpler. What do you do?", [
            ("advance_time", {"minutes": 5, "why": "time passing"})]

    def _next_step(self, context, lead):
        plan = json.loads(re.search(r"## CURRENT PLAN\n(.+)", context).group(1))
        for index, step in enumerate(plan["steps"]):
            if step["status"] == "open" and not step.get("optional"):
                return lead, [("call_for_roll", {"attempt": step["label"], "attribute": step["attribute"], "skill": step["skill"],
                                                 "difficulty": step["difficulty"], "stakes": step["stakes"], "plan_step": index})]
        return ("The film comes up on the light box: a cylinder, a tangle of wire, a battery, a small glass bulb, and something "
                "dense underneath. Whatever else this is, it is built to burn."), [
            ("record_fact", {"text": "The x-ray shows a cylinder, wiring, a battery, a glass bulb and a dense block.", "public": True}),
            ("clear_plan", {"why": "done"})]

    def _after_roll(self, attempt, result, context):
        passed = result in ("success", "critical", "messy")
        if attempt.startswith("Forcing"):
            if passed:
                return "The lid gives a finger's width, and you see a wire stretched across the gap. You stop breathing, which is easy.", [
                    ("record_fact", {"text": "The box has been tampered with, and a wire is visible under the lid.", "public": True})]
            return "A bright, chemical hiss. The box goes up in your hands, and the fire does not care what you are.", [
                ("adjust_track", {"track": "health", "amount": 3, "kind": "aggravated", "why": "thermite"}),
                ("move", {"what": "box", "to": "gone"}),
                ("record_fact", {"text": "The box burned with its contents when Alex tried to force it.", "public": True})]
        lead = "That part goes well enough." if passed else "That part goes badly, and you pay for it, but you keep going."
        return self._next_step(context, lead)
