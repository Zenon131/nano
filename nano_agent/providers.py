"Provider-scoped configuration for OpenAI-compatible endpoints."

import copy
import os
import json
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
from urllib.parse import urlparse
from openai import AsyncOpenAI

PRESETS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
    "custom": {"base_url": "", "api_key_env": "CUSTOM_API_KEY"},
}

def infer_legacy_provider(base_url: Optional[str]) -> str:
    if not base_url: return "openai"
    parsed = urlparse(base_url)
    if parsed.hostname == "openrouter.ai": return "openrouter"
    if parsed.hostname == "api.openai.com": return "openai"
    if parsed.hostname in ("localhost", "127.0.0.1", "::1") and parsed.port == 11434: return "ollama"
    return "custom"

class ProviderSettings:
    def __init__(self, config: Dict[str, Any], default_model: str = "gpt-4o"):
        self.profiles = {name: {**copy.deepcopy(preset), "model": "", "manual_models": []} for name, preset in PRESETS.items()}
        configured = config.get("providers")
        if isinstance(configured, dict) and configured:
            for name, profile in configured.items():
                if isinstance(name, str) and isinstance(profile, dict):
                    self.profiles.setdefault(name, {"base_url": "", "api_key_env": None, "model": "", "manual_models": []})
                    self.profiles[name].update(copy.deepcopy(profile))
            selected = config.get("active_provider", "openai")
            self.active = selected if selected in self.profiles else "openai"
        else:
            base_url = config.get("base_url") or os.environ.get("OPENAI_BASE_URL") or PRESETS["openai"]["base_url"]
            self.active = infer_legacy_provider(base_url)
            profile = self.profiles[self.active]
            profile["model"] = config.get("model", default_model)
            profile["base_url"] = base_url
            profile["legacy_credentials"] = True
            profile["api_key_env"] = "OPENAI_API_KEY"
            profile["api_key"] = config.get("api_key")
            profile["legacy_api_key_fallback"] = "ollama"
            profile["manual_models"] = config.get("manual_models", [])

    @property
    def current(self) -> Dict[str, Any]:
        return self.profiles[self.active]

    def credential(self) -> Optional[str]:
        if self.current.get("legacy_credentials"):
            saved = self.current.get("api_key")
            if self.active == "openrouter" and saved in (None, "", "ollama"):
                return os.environ.get("OPENROUTER_API_KEY")
            if saved is not None: return saved
            return os.environ.get("OPENAI_API_KEY", self.current.get("legacy_api_key_fallback"))
        
        saved = self.current.get("api_key")
        if saved and not (saved == "ollama" and self.active in ("openai", "openrouter")):
            return saved
        env_name = self.current.get("api_key_env")
        return os.environ.get(env_name) if env_name else ("ollama" if self.active == "ollama" else None)

    def serialized(self) -> Dict[str, Any]:
        return {"active_provider": self.active, "providers": copy.deepcopy(self.profiles)}

    def save(self):
        config_path = Path.home() / ".nano.json"
        with open(config_path, "w") as f:
            json.dump(self.serialized(), f, indent=2)

class OpenAICompatibleProvider:
    def __init__(self, settings: ProviderSettings):
        self.settings = settings
        profile = settings.current
        self.client = AsyncOpenAI(
            base_url=profile["base_url"],
            api_key=settings.credential()
        )
        self.model = profile["model"]

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages, # type: ignore
            tools=tools # type: ignore
        )
        msg = response.choices[0].message
        return {
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in (msg.tool_calls or [])
            ]
        }

    async def list_models(self) -> List[str]:
        try:
            response = await self.client.models.list()
            return [m.id for m in response.data]
        except Exception as e:
            return [f"Error fetching models: {e}"]
