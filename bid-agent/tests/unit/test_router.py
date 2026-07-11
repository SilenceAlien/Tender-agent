"""Unit tests for ModelRouter."""

import pytest

from core.llm.router import (
    DEFAULT_NODE_MODELS,
    ModelRouter,
    get_router,
    reset_router,
)


@pytest.fixture(autouse=True)
def reset():
    """Reset router singleton before each test."""
    reset_router()
    yield
    reset_router()


class TestModelRouter:
    def test_default_config_has_key_nodes(self):
        router = ModelRouter()
        assert "SectionGenerator" in DEFAULT_NODE_MODELS
        assert "QualityChecker" in DEFAULT_NODE_MODELS
        assert "ReqExtractor" in DEFAULT_NODE_MODELS

    def test_get_llm_for_section_generator(self):
        router = ModelRouter()
        router.set_api_key("deepseek", "sk-test")
        llm = router.get_llm("SectionGenerator")
        assert llm is not None

    def test_get_llm_for_quality_checker(self):
        router = ModelRouter()
        router.set_api_key("openai", "sk-test")
        llm = router.get_llm("QualityChecker")
        assert "gpt-4o-mini" in str(llm.model_name).lower() or "gpt-4o-mini" in str(getattr(llm, 'model', ''))

    def test_override_provider_per_node(self):
        config = {"SectionGenerator": {"provider": "openai", "model": "gpt-4o"}}
        router = ModelRouter(config=config)
        router.set_api_key("openai", "sk-test")
        llm = router.get_llm("SectionGenerator")
        assert llm is not None
        assert "gpt-4o" in str(llm.model_name).lower() or "gpt-4o" in str(getattr(llm, 'model', ''))

    def test_unconfigured_node_uses_default(self):
        router = ModelRouter()
        router.set_api_key("deepseek", "sk-test")
        llm = router.get_llm("UnknownNode")
        assert llm is not None

    def test_cache_same_key(self):
        router = ModelRouter()
        router.set_api_key("deepseek", "sk-test")
        llm1 = router.get_llm("SectionGenerator")
        llm2 = router.get_llm("SectionGenerator")
        assert llm1 is llm2  # cached

    def test_set_api_key_invalidates_cache(self):
        router = ModelRouter()
        router.set_api_key("deepseek", "sk-old")
        llm1 = router.get_llm("SectionGenerator")
        router.set_api_key("deepseek", "sk-new")
        llm2 = router.get_llm("SectionGenerator")
        assert llm1 is not llm2  # cache invalidated

    def test_singleton(self):
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_reset_router(self):
        r1 = get_router()
        reset_router()
        r2 = get_router()
        assert r1 is not r2
