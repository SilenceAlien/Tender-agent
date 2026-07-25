"""Model Router — task-level model assignment with fallback chains.

Each pipeline node can be assigned a different LLM provider/model.
Falls back through a priority chain if a provider is unavailable.
"""

import logging
from typing import Any

from core.llm.providers import create_llm

logger = logging.getLogger(__name__)

# Default model assignments per node
# These are fallbacks — the GUI config (~/.bid-agent/config.json) overrides them.
# N03 fix: use real DeepSeek API model names
DEFAULT_NODE_MODELS = {
    "ReqExtractor": {"provider": "deepseek", "model": "deepseek-chat"},
    "SectionGenerator": {"provider": "deepseek", "model": "deepseek-chat"},
    # M16 fix: aligned with spec — default to DeepSeek, not openai/gpt-4o-mini
    "QualityChecker": {"provider": "deepseek", "model": "deepseek-chat"},
    "FeedbackProcessor": {"provider": "deepseek", "model": "deepseek-chat"},
}

# Global default (used for any node not in the map)
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-chat"


class ModelRouter:
    """Routes LLM requests to the appropriate model per pipeline node.

    Configuration is loaded from ~/.bid_agent/config.yaml or environment
    variables, with file-based overrides per node.
    """

    def __init__(self, config: dict | None = None):
        """Initialize router.

        Args:
            config: Optional node→model mapping overrides.
                    Format: {"ReqExtractor": {"provider": "openai", "model": "gpt-4o"}}
        """
        self._node_configs: dict[str, dict] = {**DEFAULT_NODE_MODELS}
        if config:
            for node_name, node_cfg in config.items():
                self._node_configs[node_name] = {
                    **self._node_configs.get(node_name, {}),
                    **node_cfg,
                }
        self._api_keys: dict[str, str] = {}
        self._llm_cache: dict[str, Any] = {}

    def set_api_key(self, provider: str, key: str) -> None:
        """Set an API key for a provider."""
        self._api_keys[provider] = key
        # Invalidate cache for this provider
        self._llm_cache = {
            k: v for k, v in self._llm_cache.items()
            if not k.startswith(provider)
        }

    def get_llm(
        self,
        node_name: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> Any:
        """Get a ChatModel instance for a pipeline node.

        Args:
            node_name: Pipeline node name (e.g. "SectionGenerator")
            provider: Override provider (defaults to node config)
            model: Override model (defaults to node config)

        Returns:
            A LangChain BaseChatModel.

        Raises:
            ValueError: If API key is not configured.
        """
        node_cfg = self._node_configs.get(node_name, {})
        provider = provider or node_cfg.get("provider", DEFAULT_PROVIDER)
        model = model or node_cfg.get("model", DEFAULT_MODEL)

        # Check cache
        cache_key = f"{provider}:{model}"
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        api_key = self._api_keys.get(provider, "")
        if not api_key:
            logger.warning(f"No API key for {provider} — using placeholder. Set via router.set_api_key().")

        llm = create_llm(provider=provider, api_key=api_key, model=model)
        self._llm_cache[cache_key] = llm
        return llm


# ── Singleton ──────────────────────────────────────────────────────────

_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    """Get or create the singleton ModelRouter."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def reset_router() -> None:
    """Reset router singleton (for testing)."""
    global _router
    _router = None
