"""The storyteller's notebook: one JSON document per chronicle.

Everything here is bookkeeping. Plausibility is the storyteller's job. This module only
remembers what was decided, keeps secrets fixed, and makes clocks tick as time passes.
"""
import copy
import json
import os
import re
import tomllib
import uuid
from pathlib import Path

CONTENT = Path(__file__).parent / "content"
PLAYER = "player"
GONE = "gone"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "thing"


def unique_id(collection, name):
    base = slug(name)
    candidate, n = base, 2
    while candidate in collection:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def stories():
    found = []
    for path in sorted((CONTENT / "stories").glob("*.toml")):
        data = tomllib.loads(path.read_text("utf-8"))
        found.append({"id": path.stem, "title": data["title"], "name": data.get("default_name", data["title"]),
                      "pitch": data.get("pitch", "")})
    return found


def character(character_id):
    """Load a person from the reusable character library."""
    return tomllib.loads((CONTENT / "characters" / f"{character_id}.toml").read_text("utf-8"))


def new_game(story_id, name=None):
    story = tomllib.loads((CONTENT / "stories" / f"{story_id}.toml").read_text("utf-8"))
    sheet = story["player"]
    attributes = {k.lower(): v for k, v in sheet["attributes"].items()}
    people = {}
    for entry in story.get("cast", []):
        person = {**character(entry["character"]), **{k: v for k, v in entry.items() if k != "character"}}
        people[entry["character"]] = {
            "name": person["name"], "description": person.get("description", ""),
            "personality": person.get("personality", ""), "motive": person.get("motive", ""),
            "voice": person.get("voice", ""), "plan": person.get("plan", ""),
            "location": person.get("location", GONE), "attitude": person.get("attitude", ""),
            "knows": list(person.get("knows", [])), "visible": person.get("visible", True),
        }
    state = {
        "version": 1, "id": uuid.uuid4().hex[:12], "name": name or story.get("default_name", story["title"]),
        "story": story_id, "title": story["title"], "kicker": story.get("kicker", "Chronicle"),
        "briefing": story.get("briefing", ""), "premise": story.get("premise", ""), "spine": story.get("spine", ""),
        "tone": story.get("tone", ""), "opening": story.get("opening", ""),
        "start_minute": story.get("start_minute", 0), "minute": 0, "turn": 0,
        "player": {
            "name": sheet["name"], "clan": sheet.get("clan", ""), "concept": sheet.get("concept", ""),
            "attributes": attributes, "skills": {k.lower(): v for k, v in sheet.get("skills", {}).items()},
            "disciplines": sheet.get("disciplines", {}), "backgrounds": sheet.get("backgrounds", []),
            "havens": sheet.get("havens", []), "conditions": [], "hunger": sheet.get("hunger", 1),
            "health": {"max": attributes.get("stamina", 1) + 3, "superficial": 0, "aggravated": 0},
            "willpower": {"max": attributes.get("composure", 1) + attributes.get("resolve", 1),
                          "superficial": 0, "aggravated": 0},
            "location": sheet["location"],
        },
        "places": copy.deepcopy(story.get("places", {})), "people": people,
        "items": {k: {"visible": True, "tags": [], **v} for k, v in story.get("items", {}).items()},
        "facts": [{"text": text, "public": False, "minute": 0, "turn": 0} for text in story.get("facts", [])],
        "secrets": {k: {**v, "revealed": False, "how": None} for k, v in story.get("secrets", {}).items()},
        "clocks": {k: {"segments": 4, "filled": 0, "per_minutes": None, "public": False, "fired": False,
                       "on_full": "", **v, "last_tick": 0} for k, v in story.get("clocks", {}).items()},
        "threads": {k: {"public": False, **v, "open": True} for k, v in story.get("threads", {}).items()},
        "drift": [], "plan": None, "pending_roll": None, "last_input": None,
        "scene": {"title": story["places"][sheet["location"]]["name"],
                  "description": story["places"][sheet["location"]]["description"]},
        "journal": list(story.get("journal", [])), "conflict": None, "complication": None,
        "clarification": None, "ending": None, "timeline": [], "seq": 0, "summary": "", "summary_upto": 0,
        "pressure": "", "alerts": [], "log": [], "dialogue_target": None,
        "director": {"last_minute": 0, "last_turn": 0, "status": "idle", "note": ""},
    }
    if state["opening"]:
        add_message(state, "storyteller_message", state["opening"])
    return state


def add_message(state, kind, text, speaker=None, channel="storyteller", label=None):
    state["seq"] += 1
    state["timeline"].append({"seq": state["seq"], "type": kind, "text": text, "speaker": speaker,
                              "channel": channel, "label": label, "minute": state["minute"]})


def clock_time(state, minute=None):
    total = (state["start_minute"] + (state["minute"] if minute is None else minute)) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def tick_clock(state, clock_id, by=1, why=""):
    """Fill a clock. When it fills, leave an alert the storyteller must act on."""
    clock = state["clocks"][clock_id]
    clock["filled"] = max(0, min(clock["segments"], clock["filled"] + by))
    if clock["filled"] >= clock["segments"] and not clock["fired"]:
        clock["fired"] = True
        state["alerts"].append(f"Clock '{clock['name']}' has filled at {clock_time(state)}. {clock['on_full']}")


def advance_time(state, minutes):
    """Time is the one thing that moves whether or not anyone is paying attention."""
    state["minute"] += minutes
    for clock_id, clock in state["clocks"].items():
        if clock.get("per_minutes") and not clock["fired"]:
            ticks = (state["minute"] - clock["last_tick"]) // clock["per_minutes"]
            if ticks > 0:
                clock["last_tick"] += ticks * clock["per_minutes"]
                tick_clock(state, clock_id, ticks)


def damage_taken(track):
    return track["superficial"] + track["aggravated"]


def entity(state, entity_id):
    """Find a place, person or item, and return (kind, record)."""
    for kind, collection in (("place", state["places"]), ("person", state["people"]), ("item", state["items"])):
        if entity_id in collection:
            return kind, collection[entity_id]
    return None, None


def recall(state, query, limit=12):
    """Search all of history. Older detail leaves the prompt, but it never leaves the notebook."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return []
    found = []
    def consider(source, text):
        lowered = text.lower()
        hits = sum(word in lowered for word in words)
        if hits:
            found.append((hits, len(found), f"[{source}] {text}"))
    for collection in ("places", "people", "items"):
        for key, record in state[collection].items():
            consider(collection, f"{key}: " + json.dumps(record, ensure_ascii=False))
    for fact in state["facts"]:
        consider(f"fact {clock_time(state, fact['minute'])}", fact["text"])
    for entry in state["drift"]:
        consider("drift", f"{entry['change']} (because {entry['why']})")
    for message in state["timeline"]:
        consider(f"said {clock_time(state, message['minute'])}", f"{message['speaker'] or message['type']}: {message['text']}")
    found.sort(key=lambda row: (-row[0], -row[1]))
    return [row[2][:400] for row in found[:limit]]


def public_view(state):
    """Everything the browser may see. Secrets, motives and private facts stay out."""
    player = state["player"]
    here = player["location"]
    place = state["places"].get(here, {})
    people = [{"id": k, "name": p["name"], "description": p["description"]}
              for k, p in state["people"].items() if p["location"] == here and p.get("visible", True)]
    exits = [{"id": e, "name": state["places"][e]["name"]} for e in place.get("exits", []) if e in state["places"]]
    around = [{"id": k, "name": i["name"], "description": i["description"]}
              for k, i in state["items"].items() if i["at"] == here and i.get("visible", True)]
    equipment = [{"id": k, "name": i["name"], "description": i["description"]}
                 for k, i in state["items"].items() if i["at"] == PLAYER]
    roll = state["pending_roll"]
    target = state["people"].get(state["dialogue_target"] or "")
    return {
        "id": state["id"], "name": state["name"], "title": state["title"], "kicker": state["kicker"],
        "briefing": state["briefing"], "premise": state["premise"], "time": clock_time(state),
        "place": place.get("name", here), "turn": state["turn"], "scene": state["scene"],
        "character": {**{k: player[k] for k in ("name", "clan", "concept", "attributes", "skills", "disciplines",
                                                "backgrounds", "havens", "conditions", "hunger", "health", "willpower")},
                      "equipment": equipment},
        "people": people, "exits": exits, "around": around, "journal": state["journal"],
        "facts": [{"time": clock_time(state, f["minute"]), "text": f["text"]} for f in state["facts"] if f["public"]],
        "threads": [t["text"] for t in state["threads"].values() if t["open"] and t.get("public")],
        "clocks": [{"name": c["name"], "filled": c["filled"], "segments": c["segments"]}
                   for c in state["clocks"].values() if c.get("public")],
        "timeline": [{**m, "time": clock_time(state, m["minute"])} for m in state["timeline"]],
        "plan": state["plan"], "conflict": state["conflict"], "complication": state["complication"],
        "clarification": state["clarification"], "ending": state["ending"],
        "pending_roll": roll and {k: roll[k] for k in ("attempt", "attribute", "skill", "difficulty", "stakes",
                                                       "pool", "regular", "hunger", "successes", "outcome",
                                                       "passed", "rerolled")},
        "dialogue": {"target": state["dialogue_target"], "name": target["name"]} if target else None,
        "director_status": state["director"]["status"],
    }


def notebook_view(state):
    """The storyteller's private notes, for a player who chooses to look. It contains spoilers."""
    return {"summary": state["summary"], "pressure": state["pressure"], "drift": state["drift"],
            "log": state["log"][-40:], "director": state["director"], "secrets": state["secrets"],
            "clocks": state["clocks"], "threads": state["threads"],
            "people": {k: {"plan": p["plan"], "location": p["location"], "motive": p["motive"]}
                       for k, p in state["people"].items()}}


class Library:
    """Chronicles on disk. A write replaces the whole file atomically."""

    def __init__(self, root):
        self.root = Path(root)
        self.active = self.root / "games"
        self.archive_dir = self.root / "archive"
        self.active.mkdir(parents=True, exist_ok=True)

    def path(self, game_id):
        if not re.fullmatch(r"[a-f0-9]{12}", game_id or ""):
            raise KeyError(game_id)
        return self.active / f"{game_id}.json"

    def load(self, game_id):
        path = self.path(game_id)
        if not path.exists():
            raise KeyError(game_id)
        return json.loads(path.read_text("utf-8"))

    def save(self, state):
        path = self.path(state["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
        os.replace(temporary, path)

    def list(self):
        games = []
        for path in sorted(self.active.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                state = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            games.append({"id": state["id"], "name": state["name"], "title": state["title"],
                          "time": clock_time(state), "turn": state["turn"], "ended": bool(state["ending"])})
        return games

    def archive(self, game_id):
        path = self.path(game_id)
        if not path.exists():
            raise KeyError(game_id)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        os.replace(path, self.archive_dir / path.name)
