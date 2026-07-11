"""Model config persistence — save/load API keys and model preferences.

Config file: ~/.bid-agent/config.json
No API keys are logged or committed — the file stays in user home.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".bid-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "api_keys": {},
    "node_overrides": {},
    "openai_model": "gpt-4o-mini",  # BUG-12 fix: stored separately, not in api_keys
}


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Set restrictive permissions on the directory
    if hasattr(os, "chmod"):
        try:
            CONFIG_DIR.chmod(0o700)
        except OSError:
            pass


def load_config() -> dict:
    """Load model config from ~/.bid-agent/config.json.

    Returns default if file doesn't exist or is corrupted.
    """
    _ensure_dir()

    if not CONFIG_FILE.exists():
        logger.info(f"No config file at {CONFIG_FILE}, using defaults")
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        # Merge with defaults for forward-compat
        merged = {**DEFAULT_CONFIG, **config}
        merged["api_keys"] = {**DEFAULT_CONFIG["api_keys"], **config.get("api_keys", {})}
        merged["node_overrides"] = {**DEFAULT_CONFIG["node_overrides"], **config.get("node_overrides", {})}
        # BUG-12 fix: migrate oa_model from api_keys to openai_model if present
        if "oa_model" in merged["api_keys"]:
            merged["openai_model"] = merged["api_keys"].pop("oa_model")
        logger.info(f"Loaded config: provider={merged['provider']}, model={merged['model']}")
        return merged
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load config: {e}, using defaults")
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """Save model config to ~/.bid-agent/config.json."""
    _ensure_dir()

    # Don't save empty API keys
    api_keys = {k: v for k, v in config.get("api_keys", {}).items() if v}

    to_save = {
        "provider": config.get("provider", DEFAULT_CONFIG["provider"]),
        "model": config.get("model", DEFAULT_CONFIG["model"]),
        "api_keys": api_keys,
        "node_overrides": config.get("node_overrides", {}),
        "openai_model": config.get("openai_model", DEFAULT_CONFIG["openai_model"]),
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)

    if hasattr(os, "chmod"):
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass

    logger.info(f"Saved config to {CONFIG_FILE}")


def get_model_for_node(config: dict, node_name: str) -> dict:
    """Get the model config for a specific pipeline node.

    Returns: {"provider": "...", "model": "..."}
    """
    overrides = config.get("node_overrides", {})
    if node_name in overrides:
        return overrides[node_name]
    return {"provider": config["provider"], "model": config["model"]}
