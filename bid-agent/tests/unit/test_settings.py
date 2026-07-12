"""Unit tests for config loading (yaml + env overlay)."""

import os

import pytest

from config.settings import Settings, get_settings, reset_settings, _env_overlay, _cast_env_value


class TestCastEnvValue:
    def test_bool_true(self):
        assert _cast_env_value("true") is True
        assert _cast_env_value("True") is True
        assert _cast_env_value("yes") is True
        assert _cast_env_value("1") is True

    def test_bool_false(self):
        assert _cast_env_value("false") is False
        assert _cast_env_value("no") is False
        assert _cast_env_value("0") is False

    def test_int(self):
        assert _cast_env_value("42") == 42

    def test_float(self):
        assert _cast_env_value("3.14") == 3.14

    def test_none(self):
        assert _cast_env_value("null") is None
        assert _cast_env_value("none") is None

    def test_string(self):
        assert _cast_env_value("hello world") == "hello world"

    def test_list(self):
        assert _cast_env_value("a,b,c") == ["a", "b", "c"]
        assert _cast_env_value(" pdf , docx ") == ["pdf", "docx"]


class TestSettingsDefaults:
    """Verify default values from config.yaml."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_settings()


    def test_deployment_mode(self):
        s = Settings()
        assert s.deployment.mode == "local"

    def test_llm_defaults(self):
        s = Settings()
        assert s.llm.default_provider == "deepseek"
        assert s.llm.default_model == "deepseek-chat"

    def test_retrieval_defaults(self):
        s = Settings()
        assert s.retrieval.embedding_dim == 1536
        assert s.retrieval.top_k_retrieval == 8

    def test_generation_defaults(self):
        s = Settings()
        assert s.generation.max_rounds == 3
        assert s.generation.default_temperature == 0.3

    def test_document_defaults(self):
        s = Settings()
        assert s.document.chunk_size == 1000
        assert s.document.chunk_overlap == 200

    def test_paths_exist(self):
        s = Settings()
        assert s.paths.upload_dir == "data/uploads"
        assert s.paths.export_dir == "data/exports"

    def test_get_method(self):
        s = Settings()
        assert s.get("llm.default_provider") == "deepseek"
        assert s.get("nonexistent.key", "fallback") == "fallback"
        assert s.get("nonexistent") is None

    def test_get_nested(self):
        s = Settings()
        assert s.get("retrieval.embedding_dim") == 1536
        assert s.get("document.chunk_size") == 1000

    def test_get_returns_none_for_missing(self):
        s = Settings()
        assert s.get("llm.nonexistent") is None

    def test_all_returns_copy(self):
        s = Settings()
        data = s.all()
        data["llm"]["default_provider"] = "modified"
        # Original unchanged
        assert s.llm.default_provider == "deepseek"

    def test_project_root_is_absolute(self):
        s = Settings()
        assert s.project_root.is_absolute()

    def test_resolve_path(self):
        s = Settings()
        resolved = s.resolve_path("data/uploads")
        assert resolved.is_absolute()
        assert resolved.name == "uploads"

    def test_singleton(self):
        s1 = Settings()
        s2 = Settings()
        assert s1 is s2

    def test_get_settings_returns_same_instance(self):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


class TestSettingsEnvOverlay:
    """Verify environment variables override config.yaml values."""

    def setup_method(self):
        reset_settings()


    def test_override_simple_key(self, monkeypatch):
        monkeypatch.setenv("BID_DEPLOYMENT__MODE", "saas")
        s = Settings()
        assert s.deployment.mode == "saas"

    def test_override_nested_key(self, monkeypatch):
        monkeypatch.setenv("BID_LLM__DEFAULT_PROVIDER", "openai")
        s = Settings()
        assert s.llm.default_provider == "openai"

    def test_override_int_value(self, monkeypatch):
        monkeypatch.setenv("BID_RETRIEVAL__TOP_K_RETRIEVAL", "20")
        s = Settings()
        assert s.retrieval.top_k_retrieval == 20

    def test_override_float_value(self, monkeypatch):
        monkeypatch.setenv("BID_GENERATION__DEFAULT_TEMPERATURE", "0.7")
        s = Settings()
        assert s.generation.default_temperature == 0.7

    def test_override_bool_value(self, monkeypatch):
        # Not a boolean field in config, but test casting
        pass

    def test_non_bid_prefix_ignored(self, monkeypatch):
        monkeypatch.setenv("OTHER_KEY", "value")
        s = Settings()
        # No crash, defaults intact
        assert s.llm.default_provider == "deepseek"


class TestEnvOverlayFunction:
    def test_basic_overlay(self):
        config = {"llm": {"default_provider": "deepseek"}}
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BID_LLM__DEFAULT_PROVIDER", "openai")
            result = _env_overlay(config)
            assert result["llm"]["default_provider"] == "openai"

    def test_nested_new_key(self):
        config = {"llm": {}}
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BID_LLM__NEW_KEY", "hello")
            result = _env_overlay(config)
            assert result["llm"]["new_key"] == "hello"
