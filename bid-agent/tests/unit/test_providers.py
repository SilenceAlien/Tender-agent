"""Unit tests for LLM provider factory."""

import pytest

from core.llm.providers import (
    create_llm,
    list_providers,
    PROVIDER_FACTORIES,
)


class TestProviderFactory:
    def test_list_providers_returns_all(self):
        providers = list_providers()
        assert "openai" in providers
        assert "deepseek" in providers
        assert "zhipu" in providers
        assert "qwen" in providers
        assert "moonshot" in providers

    def test_create_openai(self):
        llm = create_llm("openai", api_key="sk-test", model="gpt-4o-mini")
        assert llm is not None
        assert hasattr(llm, "invoke")

    def test_create_deepseek(self):
        llm = create_llm("deepseek", api_key="sk-test", model="deepseek-v4-pro")
        assert llm is not None
        # Default base_url should be DeepSeek
        assert "deepseek.com" in str(llm.openai_api_base)

    def test_create_zhipu(self):
        llm = create_llm("zhipu", api_key="sk-test", model="glm-4")
        assert llm is not None
        assert "bigmodel.cn" in str(llm.openai_api_base)

    def test_create_qwen(self):
        llm = create_llm("qwen", api_key="sk-test", model="qwen-plus")
        assert llm is not None

    def test_create_moonshot(self):
        llm = create_llm("moonshot", api_key="sk-test", model="moonshot-v1")
        assert llm is not None

    def test_create_with_custom_base_url(self):
        llm = create_llm("openai", api_key="sk-test", model="custom-model",
                         base_url="https://custom.api.com/v1")
        assert "custom.api.com" in str(llm.openai_api_base)

    def test_create_with_temperature(self):
        llm = create_llm("openai", api_key="sk-test", model="gpt-4o",
                         temperature=0.7)
        assert llm.temperature == 0.7

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_llm("nonexistent", api_key="x", model="x")
