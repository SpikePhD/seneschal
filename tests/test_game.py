"""These tests use a scripted model, so they check the machinery rather than the storytelling."""
import json
import random
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from seneschal import dice
from seneschal.agents import build_context, director_due, director_pass, resolve_roll, take_turn
from seneschal.demo import DemoModel
from seneschal.llm import PROVIDERS, ChatClient, LLMError, Settings, parse_message
from seneschal.server import Game, Problem
from seneschal.tools import STORYTELLER_TOOLS, Toolbox, schemas
from seneschal.world import Library, add_message, advance_time, new_game, notebook_view, public_view, recall, stories


class Scripted:
    """A model that makes the given tool calls, then says the given text."""

    def __init__(self, text="The night goes on.", calls=()):
        self.text, self.calls, self.seen = text, list(calls), []

    def complete(self, messages, tools, allow_tools=True):
        self.seen.append(messages)
        if self.calls and messages[-1]["role"] != "tool":
            calls = [{"id": str(n), "name": name, "args": args} for n, (name, args) in enumerate(self.calls)]
            return {"content": "", "tool_calls": calls, "raw": {"role": "assistant", "content": ""}}
        return {"content": self.text, "tool_calls": [], "raw": {"role": "assistant", "content": self.text}}


class Failing:
    def complete(self, *_args, **_kwargs):
        raise LLMError("The model could not be reached.")


class FakeSettings:
    def __init__(self, role_models=None):
        self.role_models = role_models or {}

    def client(self, role):
        return self.role_models.get(role) or DemoModel(role)

    def public(self):
        return {}


def game_with(models=None):
    root = tempfile.mkdtemp()
    return Game(Library(root), FakeSettings(models))


class Dice(unittest.TestCase):
    def test_scoring(self):
        self.assertEqual(dice.score([6, 7, 1], [], 2)["outcome"], "success")
        self.assertEqual(dice.score([10, 10], [], 2)["successes"], 4)
        self.assertEqual(dice.score([10], [10], 2)["outcome"], "messy_critical")
        self.assertEqual(dice.score([1], [1], 1)["outcome"], "bestial_failure")
        self.assertEqual(dice.score([1], [2], 1)["outcome"], "failure")

    def test_hunger_replaces_regular_dice(self):
        result = dice.roll(4, 2, 2, random.Random(1))
        self.assertEqual((len(result["regular"]), len(result["hunger"])), (2, 2))
        self.assertEqual(len(dice.roll(1, 5, 2, random.Random(1))["hunger"]), 1)

    def test_reroll_limits(self):
        result = dice.roll(6, 1, 2, random.Random(2))
        with self.assertRaises(ValueError):
            dice.reroll(result, [0, 1, 2, 3])
        with self.assertRaises(ValueError):
            dice.reroll(result, [5])  # Only five regular dice, so index 5 would be a Hunger die.
        self.assertEqual(dice.reroll(result, [0])["hunger"], result["hunger"])


class Content(unittest.TestCase):
    def test_every_story_loads_and_is_consistent(self):
        for story in stories():
            state = new_game(story["id"])
            for person in state["people"].values():
                self.assertTrue(person["location"] == "gone" or person["location"] in state["places"], person["name"])
            for place in state["places"].values():
                for exit_id in place["exits"]:
                    self.assertIn(exit_id, state["places"])
            self.assertIn(state["player"]["location"], state["places"])
            self.assertTrue(state["timeline"], "A story should open with narration, so a new game costs no model call.")

    def test_character_library_files_have_a_will(self):
        for path in (Path("seneschal/content/characters")).glob("*.toml"):
            character = tomllib.loads(path.read_text("utf-8"))
            for field in ("name", "description", "personality", "motive", "plan", "voice"):
                self.assertTrue(character.get(field), f"{path.name} has no {field}")

    def test_deadline_lands_at_three(self):
        state = new_game("the_box")
        minutes = 3 * 60 + 24 * 60 - state["start_minute"]
        advance_time(state, minutes - 1)
        self.assertFalse(state["clocks"]["deadline"]["fired"])
        advance_time(state, 1)
        self.assertTrue(state["clocks"]["deadline"]["fired"])
        self.assertIn("03:00", state["alerts"][0])


class YourTests(unittest.TestCase):
    """The behaviours asked for: the knife, the x-ray, and opening the box."""

    def test_i_should_have_a_knife_never_errors_and_lands_in_inventory(self):
        game = game_with()
        view = game.create("the_box", "Knife")
        view = game.act(view["id"], "I look around my haven, I should have a knife", background=False)
        names = [item["name"] for item in view["character"]["equipment"]]
        self.assertIn("Worn box-cutter", names)
        self.assertEqual(view["timeline"][-1]["type"], "storyteller_message")
        # It stays real on later turns: the next prompt lists it as carried.
        state = game.library.load(view["id"])
        self.assertIn("Worn box-cutter", build_context(state).split("## CARRIED BY THE PLAYER")[1].split("##")[0])

    def test_xray_asks_how_then_offers_checks_then_rolls_them(self):
        game = game_with()
        game_id = game.create("the_box", "X-ray")["id"]
        view = game.act(game_id, "I want to x-ray the box")
        self.assertIn("How", view["clarification"]["question"])
        self.assertEqual(view["clarification"]["original"], "I want to x-ray the box")
        view = game.act(game_id, "A veterinarian's clinic has one", background=False)
        self.assertIsNone(view["clarification"])
        skills = [step["skill"] for step in view["plan"]["steps"]]
        self.assertEqual(skills, ["athletics", "stealth", "technology", "medicine"])
        self.assertIsNone(view["pending_roll"], "The player confirms a plan before anything is rolled.")
        view = game.act(game_id, "Yes, let's do it", background=False)
        rolled = 0
        while view["pending_roll"]:
            rolled += 1
            view = game.roll(game_id, "accept", background=False)
            self.assertLess(rolled, 8)
        self.assertEqual(rolled, 3, "The optional stealth step is skipped.")
        self.assertIn("cylinder", " ".join(f["text"] for f in view["facts"]))
        self.assertIsNone(view["plan"])
        state = game.library.load(game_id)
        self.assertFalse(state["secrets"]["box_contents"]["revealed"], "An x-ray shows shapes, not the whole truth.")

    def test_a_new_place_invented_in_play_becomes_real(self):
        game = game_with({"storyteller": Scripted("You find the dentist's.", [
            ("establish_place", {"name": "Dr. Kowal's Dental Surgery", "description": "A dark surgery.", "exits": ["street"]}),
            ("move", {"what": "player", "to": "dr_kowal_s_dental_surgery"})])})
        game_id = game.create("the_box", "Dentist")["id"]
        view = game.act(game_id, "A dentist has an x-ray too. I go there.", background=False)
        self.assertEqual(view["place"], "Dr. Kowal's Dental Surgery")
        self.assertEqual(view["scene"]["title"], "Dr. Kowal's Dental Surgery")
        self.assertIn("18th Street", [e["name"] for e in view["exits"]])
        self.assertIn("dr_kowal_s_dental_surgery", game.library.load(game_id)["places"]["street"]["exits"])

    def test_forcing_the_box_burns_on_failure(self):
        for seed_result, expect_gone in (("failure", True), ("success", False)):
            state = new_game("the_box")
            state = take_turn(state, DemoModel(), "I force the box open with a chisel", rng=random.Random(3))
            roll = state["pending_roll"]
            self.assertEqual(roll["attempt"], "Forcing the box open")
            roll.update(outcome=seed_result, passed=seed_result == "success", successes=4 if seed_result == "success" else 0)
            state = resolve_roll(state, DemoModel())
            self.assertEqual(state["items"]["box"]["at"] == "gone", expect_gone)
            self.assertEqual(state["player"]["health"]["aggravated"] > 0, expect_gone)
            self.assertIsNone(state["pending_roll"])


class Authority(unittest.TestCase):
    def test_model_cannot_see_dice_until_accepted(self):
        model = Scripted("You try.", [("call_for_roll", {"attempt": "Climb", "attribute": "dexterity", "skill": "athletics",
                                                         "difficulty": 2, "stakes": "A fall"})])
        state = take_turn(new_game("the_box"), model, "I climb the wall", rng=random.Random(1))
        tool_messages = [m for m in model.seen[-1] if m["role"] == "tool"]
        roll = state["pending_roll"]
        self.assertTrue(roll)
        self.assertNotIn(str(roll["regular"]), json.dumps(tool_messages))
        self.assertIn("hidden", tool_messages[0]["content"])
        self.assertNotIn("pending_roll", build_context(state))

    def test_pool_comes_from_the_sheet(self):
        state = new_game("the_box")
        Toolbox(state, rng=random.Random(1)).run("call_for_roll", {"attempt": "x", "attribute": "strength", "skill": "athletics",
                                                                   "difficulty": 2, "stakes": "y", "modifier": 1})
        self.assertEqual(state["pending_roll"]["pool"], 3 + 2 + 1)
        self.assertEqual(len(state["pending_roll"]["hunger"]), state["player"]["hunger"])

    def test_tool_problems_go_to_the_model_rather_than_the_player(self):
        state = new_game("the_box")
        box = Toolbox(state)
        self.assertIn("not a V5 skill", box.run("call_for_roll", {"attempt": "x", "attribute": "wits", "skill": "tech use", "difficulty": 2, "stakes": "y"}))
        self.assertIn("unknown place", box.run("move", {"what": "player", "to": "narnia"}))
        self.assertIn("Problem", box.run("establish_item", {"name": "x"}))
        self.assertIn("Problem", box.run("set_plan", {"goal": "x", "steps": [{"label": "nothing else"}]}))
        self.assertIn("Problem", box.run("no_such_tool", {}))
        self.assertEqual(state["player"]["location"], "haven")

    def test_secrets_cannot_be_rewritten(self):
        state = new_game("the_box")
        truth = state["secrets"]["box_contents"]["truth"]
        box = Toolbox(state)
        self.assertIn("Problem", box.run("reveal_secret", {"id": "the_box_is_empty", "how": "I said so"}))
        box.run("reveal_secret", {"id": "box_contents", "how": "x-ray", "truth": "It's empty"})
        self.assertEqual(state["secrets"]["box_contents"]["truth"], truth)
        self.assertNotIn("set_secret", STORYTELLER_TOOLS)

    def test_browser_view_holds_no_secrets(self):
        state = new_game("the_box")
        state["facts"].append({"text": "PRIVATEFACT", "public": False, "minute": 0, "turn": 0})
        view = json.dumps(public_view(state))
        for needle in ("thermite", "tilt switch", "Rook", "Sheriff", "PRIVATEFACT", "contempt", "tipped off"):
            self.assertNotIn(needle, view)
        self.assertIn("thermite", json.dumps(notebook_view(state)))  # Only if the player asks to look.

    def test_unnoticed_person_appears_once_noticed(self):
        state = new_game("the_box")
        box = Toolbox(state)
        box.run("move", {"what": "player", "to": "street"})
        self.assertNotIn("Rook", [p["name"] for p in public_view(state)["people"]])
        box.run("update", {"id": "rook", "visible": True})
        self.assertIn("Rook", [p["name"] for p in public_view(state)["people"]])

    def test_changing_canon_is_logged_not_blocked(self):
        state = new_game("the_box")
        Toolbox(state).run("update", {"id": "box", "description": "Now it is dented."})
        self.assertEqual(state["items"]["box"]["description"], "Now it is dented.")
        self.assertTrue(any(e["tool"] == "canon_changed" for e in state["log"]))


class Memory(unittest.TestCase):
    def test_prompt_does_not_grow_with_the_chronicle(self):
        """The context-window problem: a very long game has to fit, and still remember."""
        state = new_game("the_box")
        box = Toolbox(state)
        box.run("establish_item", {"name": "Grandfather's silver lighter", "description": "Engraved 'E.M.'", "at": "haven"})
        box.run("record_fact", {"text": "Alex promised Dez a favour on the night of the box.", "public": True})
        sizes = {}
        for turn in range(1, 2001):
            state["turn"] = turn
            add_message(state, "player_message", f"Turn {turn}: I do something long-winded. " * 5)
            add_message(state, "storyteller_message", f"And it happens at length, turn {turn}. " * 12)
            box.run("record_fact", {"text": f"Something happened on turn {turn}.", "public": False})
            if turn % 3 == 0:
                box.run("establish_item", {"name": f"Thing {turn}", "description": "A thing.", "at": "street"})
                box.run("establish_person", {"name": f"Stranger {turn}", "description": "Someone.", "location": "street", "motive": "Things"})
                box.run("open_thread", {"text": f"Loose end {turn}"})
                box.run("establish_place", {"name": f"Place {turn}", "description": "Somewhere."})
                box.run("create_clock", {"name": f"Clock {turn}", "segments": 4, "on_full": "It happens."})
            if turn % 10 == 0:
                state.update(summary="So far.", summary_upto=state["seq"])
            if turn in (100, 500, 2000):
                sizes[turn] = len(build_context(state))
        self.assertLess(sizes[2000], 40_000)
        self.assertLess(sizes[2000], sizes[100] * 1.6, sizes)
        self.assertIn("Grandfather's silver lighter", box.run("recall", {"query": "silver lighter"}))
        self.assertIn("promised Dez", box.run("recall", {"query": "promised favour Dez"}))

    def test_director_moves_world_summarises_and_notes_pressure(self):
        model = Scripted("", [
            ("update_summary", {"text": "Alex took the box."}),
            ("move", {"what": "rook", "to": "lakeview_clinic"}),
            ("tick_clock", {"id": "sheriff_attention", "why": "Rook reported in"}),
            ("log_drift", {"change": "Alex skipped the delivery", "why": "Alex learned what was inside"}),
            ("set_pressure", {"text": "Rook is at the clinic."}),
        ])
        state = new_game("the_box")
        state["turn"], state["minute"] = 6, 40
        self.assertTrue(director_due(state))
        before = list(state["timeline"])
        new = director_pass(state, model)
        self.assertEqual(new["summary"], "Alex took the box.")
        self.assertEqual(new["people"]["rook"]["location"], "lakeview_clinic")
        self.assertEqual(new["clocks"]["sheriff_attention"]["filled"], 1)
        self.assertEqual(new["drift"][0]["by"], "director")
        self.assertEqual(new["timeline"], before, "The director never writes to the player.")
        self.assertFalse(director_due(new))
        self.assertIn("Rook is at the clinic.", build_context(new))

    def test_director_cannot_use_player_facing_tools(self):
        box = Toolbox(new_game("the_box"), by="director")
        self.assertIn("Problem", box.run("say", {"speaker": "rook", "text": "Boo"}))
        self.assertIn("Problem", box.run("reveal_secret", {"id": "the_setup", "how": "because"}))

    def test_failed_director_does_not_stop_the_game(self):
        game = game_with({"director": Failing()})
        game_id = game.create("the_box", "x")["id"]
        for _ in range(6):
            view = game.act(game_id, "I wait.", background=False)
        state = game.library.load(game_id)
        self.assertIn("failed", state["director"]["note"])
        self.assertEqual(state["director"]["status"], "idle")


class Turns(unittest.TestCase):
    def test_a_failed_model_call_changes_nothing(self):
        game = game_with({"storyteller": Failing()})
        view = game.create("the_box", "x")
        with self.assertRaises(Problem) as caught:
            game.act(view["id"], "I wait.")
        self.assertEqual(caught.exception.code, "provider_error")
        after = public_view(game.library.load(view["id"]))
        self.assertEqual(after["timeline"], view["timeline"])
        self.assertEqual(after["turn"], 0)

    def test_every_turn_costs_time_and_clocks_tick_on_their_own(self):
        state = take_turn(new_game("the_box"), Scripted(), "I think about it.")
        self.assertEqual(state["minute"], 1)
        state = take_turn(state, Scripted("Later.", [("advance_time", {"minutes": 30, "why": "x"})]), "I wait.")
        self.assertEqual(state["minute"], 31)
        self.assertEqual(state["clocks"]["deadline"]["filled"], 2)

    def test_filled_clock_alerts_the_storyteller_once(self):
        state = new_game("the_box")
        state["clocks"]["deadline"]["filled"] = 14
        model = Scripted("It is three.", [("advance_time", {"minutes": 15, "why": "x"})])
        state = take_turn(state, model, "I wait.")
        self.assertTrue(state["clocks"]["deadline"]["fired"])
        self.assertIn("ALERTS", [m for m in model.seen[-1] if m["role"] == "tool"][0]["content"])
        self.assertEqual(state["alerts"], [])

    def test_dialogue_reaches_npc_and_asides_do_not(self):
        model = Scripted("A beat.", [("say", {"speaker": "dez_okafor", "text": "Hey you."})])
        state = new_game("the_box")
        state["player"]["location"] = "lakeview_clinic"
        state = take_turn(state, model, "Hi Dez", channel="dialogue", target="dez_okafor")
        speech = [m for m in state["timeline"] if m["type"] == "npc_message"]
        self.assertEqual((speech[0]["speaker"], speech[0]["channel"]), ("Dez Okafor", "dialogue"))
        self.assertIn('says these exact words to Dez Okafor', model.seen[0][1]["content"])
        aside = Scripted()
        take_turn(state, aside, "Would she lie to me?", channel="dialogue", target="dez_okafor", recipient="storyteller")
        self.assertIn("does not hear this", aside.seen[0][1]["content"])

    def test_roll_and_reroll_rules(self):
        game = game_with()
        game_id = game.create("the_box", "x")["id"]
        view = game.act(game_id, "I force the box open")
        self.assertTrue(view["pending_roll"])
        with self.assertRaises(Problem) as caught:
            game.act(game_id, "I do something else")
        self.assertEqual(caught.exception.code, "pending_roll")
        rerolled = game.roll(game_id, "reroll", [0])
        self.assertEqual(rerolled["character"]["willpower"]["superficial"], 1)
        self.assertTrue(rerolled["pending_roll"]["rerolled"])
        with self.assertRaises(Problem) as caught:
            game.roll(game_id, "reroll", [0])
        self.assertEqual(caught.exception.code, "invalid_reroll")
        self.assertIsNone(game.roll(game_id, "accept", background=False)["pending_roll"])

    def test_messy_results_raise_a_complication(self):
        state = new_game("the_box")
        Toolbox(state, rng=random.Random(1)).run("call_for_roll", {"attempt": "x", "attribute": "wits", "skill": "stealth", "difficulty": 2, "stakes": "y"})
        state["pending_roll"].update(outcome="bestial_failure", passed=False, successes=0)
        new = resolve_roll(state, Scripted())
        self.assertIn("Bestial", new["complication"])

    def test_chronicle_end_blocks_more_turns(self):
        game = game_with({"storyteller": Scripted("It ends.", [("end_chronicle", {"outcome": "Burned", "summary": "Ash."})])})
        game_id = game.create("the_box", "x")["id"]
        self.assertEqual(game.act(game_id, "I end it.")["ending"]["outcome"], "Burned")
        with self.assertRaises(Problem):
            game.act(game_id, "One more thing")

    def test_empty_model_reply_still_answers_the_player(self):
        class Silent(Scripted):
            def complete(self, messages, tools, allow_tools=True):
                if not tools:
                    return {"content": "Recovered reply.", "tool_calls": [], "raw": {}}
                return {"content": "", "tool_calls": [], "raw": {}}
        state = take_turn(new_game("the_box"), Silent(), "Hello?")
        self.assertEqual(state["timeline"][-1]["text"], "Recovered reply.")


class RealClientShape(unittest.TestCase):
    """Runs the real HTTP client against a local stub. It checks the protocol, not any actual model."""

    def serve(self, responses):
        seen = []

        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append({"auth": self.headers["Authorization"], "body": body})
                status, payload = responses.pop(0)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        original = dict(PROVIDERS["openai"])
        PROVIDERS["openai"]["url"] = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        self.addCleanup(lambda: PROVIDERS["openai"].update(original))
        return seen

    def test_a_full_tool_round_over_http(self):
        seen = self.serve([
            (200, {"choices": [{"message": {"content": None, "tool_calls": [{"id": "abc", "type": "function", "function": {
                "name": "establish_item", "arguments": json.dumps({"name": "Knife", "description": "Short.", "at": "player"})}}]}}]}),
            (200, {"choices": [{"message": {"content": "You find a knife."}}]}),
        ])
        state = take_turn(new_game("the_box"), ChatClient("openai", "test-model", "medium", "sk-secret"), "I should have a knife")
        self.assertEqual(state["items"]["knife"]["at"], "player")
        self.assertEqual(state["timeline"][-1]["text"], "You find a knife.")
        first, second = seen[0], seen[1]
        self.assertEqual(first["auth"], "Bearer sk-secret")
        self.assertEqual(first["body"]["model"], "test-model")
        self.assertEqual(first["body"]["reasoning_effort"], "medium")
        self.assertIn("max_completion_tokens", first["body"])
        self.assertTrue(first["body"]["tools"])
        assistant, tool = second["body"]["messages"][-2:]
        self.assertEqual(assistant["tool_calls"][0]["id"], "abc")
        self.assertEqual((tool["role"], tool["tool_call_id"]), ("tool", "abc"))
        self.assertNotIn("sk-secret", json.dumps(state))

    def test_provider_errors_are_readable_and_hide_the_key(self):
        self.serve([(401, {"error": {"message": "Bad key sk-secret"}})])
        with self.assertRaises(LLMError) as caught:
            ChatClient("openai", "m", "none", "sk-secret").complete([{"role": "user", "content": "hi"}], [])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("sk-secret", str(caught.exception))


class Plumbing(unittest.TestCase):
    def test_interrupted_director_does_not_stay_working_forever(self):
        game = game_with()
        game_id = game.create("the_box", "x")["id"]
        state = game.library.load(game_id)
        state["director"]["status"] = "working"
        game.library.save(state)
        self.assertEqual(game.view(game_id)["director_status"], "idle")

    def test_going_somewhere_makes_the_way_back_usable(self):
        state = new_game("the_box")
        Toolbox(state).run("move", {"what": "player", "to": "lakeview_clinic"})
        self.assertIn("lakeview_clinic", state["places"]["haven"]["exits"])
        self.assertIn("haven", state["places"]["lakeview_clinic"]["exits"])

    def test_tool_schemas_are_well_formed(self):
        for schema in schemas(STORYTELLER_TOOLS):
            parameters = schema["function"]["parameters"]
            for required in parameters["required"]:
                self.assertIn(required, parameters["properties"], schema["function"]["name"])

    def test_every_tool_is_implemented(self):
        for name in STORYTELLER_TOOLS:
            self.assertTrue(hasattr(Toolbox, "tool_" + name), name)

    def test_parse_mistral_style_chunks_and_bad_json_arguments(self):
        parsed = parse_message({"content": [{"type": "thinking", "text": "hm"}, {"type": "text", "text": "Hello"}],
                                "tool_calls": [{"id": "1", "function": {"name": "move", "arguments": "{bad"}}]})
        self.assertEqual(parsed["content"], "Hello")
        self.assertIn("Problem", Toolbox(new_game("the_box")).run("move", parsed["tool_calls"][0]["args"]))

    def test_settings_never_store_keys_and_default_to_available_provider(self):
        root = tempfile.mkdtemp()
        env = Path(root) / ".env"
        env.write_text('OPENAI_API_KEY="sk-test-secret"\n')
        settings = Settings(root, env)
        settings.env.pop("MISTRAL_API_KEY", None)
        self.assertTrue(settings.public()["credentials"]["openai"])
        self.assertNotIn("sk-test-secret", json.dumps(settings.public()))
        settings.save(settings.data["profiles"], {"storyteller": "openai", "director": "offline"})
        self.assertNotIn("sk-test-secret", (Path(root) / "settings.json").read_text())
        with self.assertRaises(ValueError):
            Settings(root).save(settings.data["profiles"], {"storyteller": "nonsense"})

    def test_recall_finds_only_matches(self):
        state = new_game("the_box")
        self.assertTrue(recall(state, "lead seal"))
        self.assertEqual(recall(state, "zzzzz"), [])


if __name__ == "__main__":
    unittest.main()
