"""Global configuration loader — reads config.yaml and overlays environment variables.

Environment variable format: BID_<section>__<key> (double-underscore for nesting).
Example: BID_LLM__DEFAULT_PROVIDER=openai overrides llm.default_provider.
"""

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ── Load .env if present ───────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ── Path resolution ────────────────────────────────────────────────────


def _get_config_dir() -> Path:
    """Absolute path to the config directory."""
    return Path(__file__).resolve().parent


def _get_project_root() -> Path:
    return _PROJECT_ROOT


# ── YAML loading ───────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override dict into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ── Environment variable overlay ───────────────────────────────────────


def _env_overlay(config: dict, prefix: str = "BID_") -> dict:
    """Overlay config values from environment variables.

    Maps BID_SECTION__KEY → config["section"]["key"]
    Nested: BID_LLM__DEFAULT_PROVIDER → config["llm"]["default_provider"]
    """
    result = config.copy()

    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue

        # Strip prefix: BID_LLM__DEFAULT_PROVIDER → llm__default_provider
        key_path = env_key[len(prefix):].lower().split("__")

        # Walk into nested dict, creating intermediate levels
        target = result
        for i, part in enumerate(key_path[:-1]):
            if part not in target:
                target[part] = {}
            if not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]

        target[key_path[-1]] = _cast_env_value(env_value)

    return result


def _cast_env_value(value: str) -> Any:
    """Cast environment variable string to appropriate Python type."""
    lower = value.lower()
    if lower in ("true", "yes", "1"):
        return True
    if lower in ("false", "no", "0"):
        return False
    if lower in ("null", "none", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # Support comma-separated lists: "pdf,docx" → ["pdf", "docx"]
    if "," in value:
        return [item.strip() for item in value.split(",")]
    return value


# ── Settings singleton ─────────────────────────────────────────────────


class Settings:
    """Immutable-ish settings container with dot-notation access."""

    _instance: "Settings | None" = None
    _data: dict

    def __new__(cls) -> "Settings":
        if cls._instance is not None:
            return cls._instance
        instance = super().__new__(cls)
        cls._instance = instance
        return instance

    def __init__(self):
        # Skip re-init if already initialized (singleton)
        if hasattr(self, "_data"):
            return
        # Load base config
        config_path = _get_config_dir() / "config.yaml"
        config = _load_yaml(config_path)

        # Load local overrides (config.local.yaml, git-ignored)
        local_path = _get_config_dir() / "config.local.yaml"
        if local_path.exists():
            local = _load_yaml(local_path)
            config = _deep_merge(config, local)

        # Apply environment variable overlay
        config = _env_overlay(config)

        self._data = config
        self._project_root = _get_project_root()
        # Also update the global singleton reference in this module
        _update_global_singleton(self)

    @classmethod
    def get_instance(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            value = self._data[name]
            if isinstance(value, dict):
                return _DotDict(value)
            return value
        raise AttributeError(f"Settings has no key '{name}'")

    def get(self, key: str, default: Any = None) -> Any:
        """Dotted-path getter: settings.get('llm.default_provider')."""
        parts = key.split(".")
        value = self._data
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def all(self) -> dict:
        """Return a deep copy of the full config dict (safe to mutate)."""
        return copy.deepcopy(self._data)

    @property
    def project_root(self) -> Path:
        return self._project_root

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a relative path from the project root."""
        return self._project_root / relative_path


class _DotDict:
    """Recursive dot-notation wrapper for nested dicts."""

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = object.__getattribute__(self, "_data")
        if name in data:
            value = data[name]
            if isinstance(value, dict):
                return _DotDict(value)
            return value
        raise AttributeError(f"No key '{name}'")

    def __repr__(self) -> str:
        data = object.__getattribute__(self, "_data")
        return f"_DotDict({data})"

    def to_dict(self) -> dict:
        return object.__getattribute__(self, "_data").copy()


# ── Module-level singleton ─────────────────────────────────────────────

# Lazy-init — first access creates the singleton, env vars respected.
settings: Settings | None = None


def _update_global_singleton(instance: Settings) -> None:
    """Sync the module-level variable with the class-level instance."""
    global settings
    settings = instance


def reset_settings() -> None:
    """Reset both module-level and class-level singleton (for testing)."""
    global settings
    settings = None
    Settings._instance = None


def get_settings() -> Settings:
    global settings
    if settings is None:
        settings = Settings()
    return settings
