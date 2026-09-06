"""Pipeline integration — wires configured LLM models into the LangGraph pipeline."""

import logging
from typing import Callable

from core.config_persistence import load_config, get_model_for_node
from core.llm.router import ModelRouter

logger = logging.getLogger(__name__)


def _make_llm_fn(llm) -> Callable[[str], str]:
    """Create a safe LLM callable that invokes exactly once per prompt.

    Previously the inline lambda called _llm.invoke(prompt) 2-3 times
    (once for hasattr, once for .content, potentially again for str()).
    This wrapper caches the result to avoid double token spend.
    """
    def fn(prompt: str) -> str:
        result = llm.invoke(prompt)
        if hasattr(result, "content"):
            return result.content
        return str(result)
    return fn


def build_llm_for_pipeline(
    validate_keys: bool = True,
    show_progress: Callable[[str], None] | None = None,
) -> dict[str, Callable[[str], str] | None]:
    """Build LLM functions for pipeline nodes from saved config.

    Args:
        validate_keys: If True, validates API keys before building (default True).
                       Set False for testing or when validation is done elsewhere.
        show_progress: Optional callback for progress messages (e.g. st.info).

    Returns:
        Dict mapping node_name -> llm_fn(prompt: str) -> str
        If a node's provider has no API key configured, returns None (mock).

    Raises:
        ConnectionError: If validate_keys=True and any provider key is invalid.
    """
    config = load_config()
    api_keys = config.get("api_keys", {})

    router = ModelRouter()
    for provider, key in api_keys.items():
        if key:
            router.set_api_key(provider, key)

    # ── Pre-flight: validate API keys before building LLM functions ─────
    if validate_keys:
        # Collect unique (provider, model) combos to validate
        nodes = ["ReqExtractor", "ContractExtractor", "SectionGenerator", "QualityChecker", "FeedbackProcessor", "ScoreSimulator"]
        seen_combos: set[tuple[str, str]] = set()

        for node in nodes:
            model_cfg = get_model_for_node(config, node)
            provider = model_cfg["provider"]
            model = model_cfg["model"]
            combo = (provider, model)

            if combo in seen_combos:
                continue
            seen_combos.add(combo)

            key = api_keys.get(provider, "")
            if not key:
                continue  # Skip providers without keys (they'll use mock)

            if show_progress:
                show_progress(f"验证 {provider}/{model} API Key...")

            from core.llm.providers import test_connection
            ok, msg = test_connection(provider, api_key=key, model=model)
            if not ok:
                raise ConnectionError(
                    f"API Key 验证失败 — {provider}/{model}: {msg}"
                )
            logger.info(f"Pre-flight OK: {provider}/{model}")

    # ── Build LLM functions ─────────────────────────────────────────────
    nodes = ["ReqExtractor", "ContractExtractor", "SectionGenerator", "QualityChecker", "FeedbackProcessor", "ScoreSimulator"]
    llm_fns: dict[str, Callable[[str], str] | None] = {}

    for node in nodes:
        model_cfg = get_model_for_node(config, node)
        provider = model_cfg["provider"]
        model = model_cfg["model"]

        if provider in api_keys and api_keys[provider]:
            try:
                llm = router.get_llm(node, provider=provider, model=model)
                llm_fns[node] = _make_llm_fn(llm)
                logger.info(f"Node {node}: using {provider}/{model}")
            except Exception as e:
                logger.warning(f"Node {node}: failed to init {provider}/{model}: {e}")
                llm_fns[node] = None
        else:
            logger.warning(f"Node {node}: no API key for {provider}, using mock")
            llm_fns[node] = None

    return llm_fns

