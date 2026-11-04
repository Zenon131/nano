"""Provider-scoped configuration for OpenAI-compatible endpoints."""

import copy
import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse


PRESETS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
    "custom": {"base_url": "", "api_key_env": "CUSTOM_API_KEY"},
}


def infer_legacy_provider(base_url: Optional[str]) -> str:
    """Identify a legacy endpoint without assuming that a model ID implies a provider."""
    if not base_url:
        return "openai"
    parsed = urlparse(base_url)
    if parsed.hostname == "openrouter.ai":
        return "openrouter"
    if parsed.hostname == "api.openai.com":
        return "openai"
    if parsed.hostname in ("localhost", "127.0.0.1", "::1") and parsed.port == 11434:
        return "ollama"
    return "custom"


class ProviderSettings:
    """Maintain independent provider endpoints, model choices and credentials."""

    def __init__(self, config: Dict[str, Any], default_model: str = "gpt-4o"):
        self.profiles = {
            name: {**copy.deepcopy(preset), "model": "", "manual_models": []}
            for name, preset in PRESETS.items()
        }
        configured = config.get("providers")
        if isinstance(configured, dict) and configured:
            for name, profile in configured.items():
                if isinstance(name, str) and isinstance(profile, dict):
                    self.profiles.setdefault(name, {"base_url": "", "api_key_env": None, "model": "", "manual_models": []})
                    self.profiles[name].update(copy.deepcopy(profile))
            selected = config.get("active_provider", "openai")
            self.active = selected if selected in self.profiles else "openai"
        else:
            # The old client passed None through to the SDK, which resolved
            # OPENAI_BASE_URL before falling back to OpenAI's default endpoint.
            base_url = config.get("base_url")
            if base_url is None:
                base_url = os.environ.get("OPENAI_BASE_URL")
            if base_url is None:
                base_url = PRESETS["openai"]["base_url"]
            self.active = infer_legacy_provider(base_url)
            profile = self.profiles[self.active]
            profile["model"] = config.get("model", default_model)
            profile["base_url"] = base_url
            profile["legacy_credentials"] = True
            profile["api_key_env"] = "OPENAI_API_KEY"
            if "api_key" in config:
                profile["api_key"] = config["api_key"]
            else:
                profile["legacy_api_key_fallback"] = "ollama"
            if isinstance(config.get("manual_models"), list):
                profile["manual_models"] = list(config["manual_models"])

    @property
    def current(self) -> Dict[str, Any]:
        return self.profiles[self.active]

    def credential(self) -> Optional[str]:
        """Resolve the active profile's credential, preserving legacy fallback rules."""
        if self.current.get("legacy_credentials"):
            saved = self.current.get("api_key")
            if self.active == "openrouter" and saved in (None, "", "ollama"):
                router_key = os.environ.get("OPENROUTER_API_KEY")
                if router_key:
                    return router_key
            if saved is not None:
                return saved
            return os.environ.get("OPENAI_API_KEY", self.current.get("legacy_api_key_fallback"))
        saved = self.current.get("api_key")
        if saved and not (saved == "ollama" and self.active in ("openai", "openrouter")):
            return saved
        env_name = self.current.get("api_key_env")
        if env_name:
            return os.environ.get(env_name)
        return "ollama" if self.active == "ollama" else None

    def serialized(self) -> Dict[str, Any]:
        return {"active_provider": self.active, "providers": copy.deepcopy(self.profiles)}
