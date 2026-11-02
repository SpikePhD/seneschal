"""A thin client for chat-completions APIs with tool calling, plus settings and credentials."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = {
    "mistral": {"url": "https://api.mistral.ai/v1/chat/completions", "key": "MISTRAL_API_KEY", "label": "Mistral",
                "model": "mistral-medium-3-5", "effort": "high", "efforts": ["none", "high"], "tokens": "max_tokens"},
    "openai": {"url": "https://api.openai.com/v1/chat/completions", "key": "OPENAI_API_KEY", "label": "OpenAI",
               "model": "gpt-5.6-luna", "effort": "medium", "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
               "tokens": "max_completion_tokens"},
}
ROLES = ("storyteller", "director")
OFFLINE = "offline"


class LLMError(Exception):
    """The model could not be reached. The message is safe to show and holds no credentials."""


def read_env_file(path):
    values = {}
    if path and Path(path).exists():
        for line in Path(path).read_text("utf-8-sig").splitlines():
            name, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and not name.startswith("#"):
                value = value.strip()
                values[name.strip()] = value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value
    return values


class Settings:
    """Model choices live beside the saved games. API keys stay in the environment or .env."""

    def __init__(self, root, env_file=None):
        self.path = Path(root) / "settings.json"
        self.env = {**read_env_file(env_file), **os.environ}
        self.data = self._load()

    def _defaults(self):
        profiles = {name: {"model": self.env.get(f"{name.upper()}_MODEL", p["model"]),
                           "reasoning_effort": self.env.get(f"{name.upper()}_REASONING_EFFORT", p["effort"])}
                    for name, p in PROVIDERS.items()}
        available = next((name for name, p in PROVIDERS.items() if self.env.get(p["key"])), OFFLINE)
        return {"profiles": profiles, "roles": {role: available for role in ROLES}}

    def _load(self):
        data = self._defaults()
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text("utf-8"))
                data["profiles"].update(saved.get("profiles", {}))
                data["roles"].update(saved.get("roles", {}))
            except (OSError, ValueError):
                pass
        return data

    def public(self):
        return {**self.data,
                "credentials": {n: bool(self.env.get(p["key"])) for n, p in PROVIDERS.items()},
                "reasoning_options": {n: p["efforts"] for n, p in PROVIDERS.items()},
                "providers": {n: p["label"] for n, p in PROVIDERS.items()}}

    def save(self, profiles, roles):
        for name, profile in profiles.items():
            if name not in PROVIDERS:
                raise ValueError("Unknown provider.")
            if profile.get("reasoning_effort") not in PROVIDERS[name]["efforts"]:
                raise ValueError(f"Unsupported reasoning effort for {PROVIDERS[name]['label']}.")
            if not str(profile.get("model", "")).strip():
                raise ValueError(f"Enter a model ID for {PROVIDERS[name]['label']}.")
        for role, provider in roles.items():
            if role not in ROLES or (provider != OFFLINE and provider not in PROVIDERS):
                raise ValueError("Unknown role or provider.")
            if provider != OFFLINE and not self.env.get(PROVIDERS[provider]["key"]):
                raise ValueError(f"No API key is set for {PROVIDERS[provider]['label']}.")
        self.data = {"profiles": profiles, "roles": roles}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")

    def client(self, role):
        provider = self.data["roles"].get(role, OFFLINE)
        if provider == OFFLINE:
            from .demo import DemoModel
            return DemoModel(role)
        profile = self.data["profiles"][provider]
        key = self.env.get(PROVIDERS[provider]["key"])
        if not key:
            raise LLMError(f"No API key is set for {PROVIDERS[provider]['label']}.")
        return ChatClient(provider, profile["model"], profile["reasoning_effort"], key)


class ChatClient:
    def __init__(self, provider, model, effort, key, timeout=180, max_tokens=8192):
        self.config = PROVIDERS[provider]
        self.model, self.effort, self.key, self.timeout, self.max_tokens = model, effort, key, timeout, max_tokens

    def complete(self, messages, tools, allow_tools=True):
        """Return {'content': str, 'tool_calls': [...], 'raw': assistant message to send back}."""
        payload = {"model": self.model, "messages": messages, self.config["tokens"]: self.max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto" if allow_tools else "none"
        if self.effort and self.effort != "none":
            payload["reasoning_effort"] = self.effort
        request = urllib.request.Request(
            self.config["url"], data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise LLMError(f"{self.config['label']} returned HTTP {error.code}: {self._reason(error)}") from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise LLMError(f"{self.config['label']} could not be reached.") from None
        try:
            message = document["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"{self.config['label']} returned an unexpected response.") from None
        return parse_message(message)

    def _reason(self, error):
        try:
            detail = json.loads(error.read())
            text = detail.get("error", detail)
            text = text.get("message", "") if isinstance(text, dict) else str(detail.get("message", text))
        except (ValueError, OSError, AttributeError):
            text = ""
        return text.replace(self.key, "[key]")[:300] or "no detail"


def parse_message(message):
    content = message.get("content") or ""
    if isinstance(content, list):  # Some models return thinking and text as separate chunks.
        content = "".join(chunk.get("text", "") for chunk in content
                          if isinstance(chunk, dict) and chunk.get("type") == "text")
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError:
                arguments = {"__unparsed__": arguments}
        calls.append({"id": call.get("id", ""), "name": function.get("name", ""), "args": arguments})
    raw = {"role": "assistant", "content": content}
    if message.get("tool_calls"):
        raw["tool_calls"] = message["tool_calls"]
    return {"content": content.strip(), "tool_calls": calls, "raw": raw}
