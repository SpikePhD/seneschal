"""A local web server. It binds to this machine only and rejects requests from other sites."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import dice
from .agents import director_due, director_pass, resolve_roll, take_turn
from .llm import LLMError
from .world import Library, new_game, notebook_view, public_view, stories

WEB = Path(__file__).parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY = 200_000
MAX_INPUT = 2000


class Problem(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Game:
    """The operations behind the API, kept apart from HTTP so they can be tested directly."""

    def __init__(self, library, settings):
        self.library, self.settings = library, settings
        self._locks, self._guard, self._running = {}, threading.Lock(), set()

    def lock(self, game_id):
        with self._guard:
            return self._locks.setdefault(game_id, threading.RLock())

    def load(self, game_id):
        try:
            return self.library.load(game_id)
        except KeyError:
            raise Problem(404, "not_found", "That chronicle does not exist.") from None

    def view(self, game_id):
        with self._guard:
            running = game_id in self._running
        state = self.load(game_id)
        if state["director"]["status"] == "working" and not running:
            state["director"]["status"] = "idle"
        return public_view(state)

    def create(self, story, name):
        known = {s["id"] for s in stories()}
        if story not in known:
            raise Problem(400, "unknown_story", "That story does not exist.")
        state = new_game(story, (name or "").strip()[:120] or None)
        self.library.save(state)
        return public_view(state)

    def act(self, game_id, text, channel="storyteller", target=None, recipient="npc", background=True):
        text = (text or "").strip()
        if not 1 <= len(text) <= MAX_INPUT:
            raise Problem(400, "invalid_input", f"Write between 1 and {MAX_INPUT} characters.")
        with self.lock(game_id):
            state = self.load(game_id)
            self._must_be_open(state)
            if state["pending_roll"]:
                raise Problem(409, "pending_roll", "A roll is waiting for your decision.")
            new = self._model_step(lambda client: take_turn(state, client, text, channel, target, recipient))
            return self._commit(new, background)

    def roll(self, game_id, action, chosen=None, background=True):
        with self.lock(game_id):
            state = self.load(game_id)
            self._must_be_open(state)
            pending = state["pending_roll"]
            if not pending:
                raise Problem(409, "no_pending_roll", "No roll is waiting.")
            if action == "reroll":
                willpower = state["player"]["willpower"]
                if pending["rerolled"]:
                    raise Problem(400, "invalid_reroll", "That roll has already been rerolled.")
                if willpower["superficial"] + willpower["aggravated"] >= willpower["max"]:
                    raise Problem(400, "invalid_reroll", "You have no Willpower left to spend.")
                try:
                    state["pending_roll"] = {**dice.reroll(pending, chosen or []), "rerolled": True}
                except ValueError as error:
                    raise Problem(400, "invalid_reroll", str(error)) from None
                willpower["superficial"] += 1
                self.library.save(state)
                return public_view(state)
            if action != "accept":
                raise Problem(400, "invalid_action", "Unknown roll action.")
            new = self._model_step(lambda client: resolve_roll(state, client))
            return self._commit(new, background)

    def set_dialogue(self, game_id, target):
        with self.lock(game_id):
            state = self.load(game_id)
            if target is not None and state["people"].get(target, {}).get("location") != state["player"]["location"]:
                raise Problem(400, "not_present", "They are not here.")
            state["dialogue_target"] = target
            self.library.save(state)
            return public_view(state)

    def archive(self, game_id):
        with self.lock(game_id):
            try:
                self.library.archive(game_id)
            except KeyError:
                raise Problem(404, "not_found", "That chronicle does not exist.") from None

    def _must_be_open(self, state):
        if state["ending"]:
            raise Problem(409, "ended", "This chronicle has ended.")

    def _model_step(self, step):
        """The state is only replaced if the whole turn succeeds, so a failed call changes nothing."""
        try:
            return step(self.settings.client("storyteller"))
        except LLMError as error:
            raise Problem(502, "provider_error", str(error)) from None

    def _commit(self, new, background):
        due = director_due(new)
        if due:
            new["director"]["status"] = "working"
        self.library.save(new)
        view = public_view(new)
        if due:
            with self._guard:
                self._running.add(new["id"])
            if background:
                threading.Thread(target=self.run_director, args=(new["id"],), daemon=True).start()
            else:
                self.run_director(new["id"])
        return view

    def run_director(self, game_id):
        """If the director fails, the game simply carries on and it tries again later."""
        with self.lock(game_id):
            state = self.library.load(game_id)
            try:
                new = director_pass(state, self.settings.client("director"))
            except Exception as error:  # A failed background pass must never take the game down with it.
                new = state
                new["director"].update(status="idle", note=f"Last pass failed: {type(error).__name__}: {error}"[:300],
                                       last_turn=new["turn"], last_minute=new["minute"])
            self.library.save(new)
            with self._guard:
                self._running.discard(game_id)


def make_handler(game, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _local_only(self):
            if self.headers.get("Host") not in allowed_hosts:
                raise Problem(403, "forbidden", "Local requests only.")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc not in allowed_hosts:
                raise Problem(403, "forbidden", "Local requests only.")

        def _body(self):
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise Problem(415, "json_required", "Send JSON.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise Problem(413, "too_large", "That request is too large.")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise Problem(400, "bad_json", "That request could not be read.") from None
            if not isinstance(data, dict):
                raise Problem(400, "bad_json", "That request could not be read.")
            return data

        def _dispatch(self, handler):
            try:
                self._local_only()
                handler()
            except Problem as problem:
                self._json(problem.status, {"code": problem.code, "message": problem.message})
            except Exception as error:  # The player gets a retry, and their words are kept in the browser.
                self._json(500, {"code": "operation_failed", "message": f"Something went wrong: {type(error).__name__}."})

        def do_GET(self):
            self._dispatch(self._get)

        def do_POST(self):
            self._dispatch(self._post)

        def do_DELETE(self):
            self._dispatch(self._delete)

        def _get(self):
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            if path == "/api/bootstrap":
                return self._json(200, {"games": game.library.list(), "stories": stories(),
                                        "settings": game.settings.public()})
            if path == "/api/settings":
                return self._json(200, game.settings.public())
            if len(parts) == 3 and parts[:2] == ["api", "games"]:
                return self._json(200, game.view(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "games"] and parts[3] == "notebook":
                return self._json(200, notebook_view(game.load(parts[2])))
            name = "index.html" if path == "/" else path.lstrip("/")
            target = WEB / name
            if "/" in name or target.suffix not in TYPES or not target.is_file():
                raise Problem(404, "not_found", "Not found.")
            self._send(200, target.read_bytes(), TYPES[target.suffix])

        def _post(self):
            parts = urlsplit(self.path).path.strip("/").split("/")
            body = self._body()
            if parts == ["api", "games"]:
                return self._json(201, game.create(body.get("story", "the_box"), body.get("name")))
            if parts == ["api", "settings"]:
                try:
                    game.settings.save(body.get("profiles", {}), body.get("roles", {}))
                except ValueError as error:
                    raise Problem(400, "invalid_settings", str(error)) from None
                return self._json(200, game.settings.public())
            if len(parts) == 4 and parts[:2] == ["api", "games"]:
                game_id, action = parts[2], parts[3]
                if action == "act":
                    return self._json(200, game.act(game_id, body.get("text"), body.get("channel", "storyteller"),
                                                    body.get("target"), body.get("recipient", "npc")))
                if action == "roll":
                    return self._json(200, game.roll(game_id, body.get("action"), body.get("dice")))
                if action == "dialogue":
                    return self._json(200, game.set_dialogue(game_id, body.get("target")))
            raise Problem(404, "not_found", "Not found.")

        def _delete(self):
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "games"]:
                game.archive(parts[2])
                return self._json(200, {"archived": parts[2]})
            raise Problem(404, "not_found", "Not found.")

    return Handler


def serve(data_dir, settings, port=8765):
    game = Game(Library(data_dir), settings)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(game, port))
    print(f"Seneschal is running at http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
