"""LLM provider factory — multi-model adapter layer.

Supports both official ChatOpenAI and ChatDeepSeek (via openai-compatible API).

Provider base URLs are sourced from official documentation:
- DeepSeek: https://api-docs.deepseek.com/zh-cn/
- OpenAI:  https://platform.openai.com/docs/
- Zhipu:   https://open.bigmodel.cn/dev/api/
- Qwen:    https://help.aliyun.com/zh/model-studio/
- Moonshot: https://platform.moonshot.cn/docs/
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Provider registry ──────────────────────────────────────────────────


def _create_openai(api_key: str, model: str, base_url: str | None = None, **kwargs) -> Any:
    """Create a ChatOpenAI instance."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=api_key or "sk-placeholder",
        model=model,
        base_url=base_url,
        temperature=kwargs.get("temperature", 0.1),
        max_tokens=kwargs.get("max_tokens", 4096),
    )


def _create_deepseek(api_key: str, model: str, base_url: str | None = None, **kwargs) -> Any:
    """Create a ChatDeepSeek via openai-compatible API.

    Official base URL: https://api.deepseek.com (ref: api-docs.deepseek.com/zh-cn/)
    """
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=api_key or "sk-placeholder",
        model=model,
        base_url=base_url or "https://api.deepseek.com",
        temperature=kwargs.get("temperature", 0.1),
        max_tokens=kwargs.get("max_tokens", 4096),
    )


def _create_zhipu(api_key: str, model: str, base_url: str | None = None, **kwargs) -> Any:
    """Create a ChatZhipuAI via openai-compatible API."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=api_key or "sk-placeholder",
        model=model,
        base_url=base_url or "https://open.bigmodel.cn/api/paas/v4",
        temperature=kwargs.get("temperature", 0.1),
        max_tokens=kwargs.get("max_tokens", 4096),
    )


def _create_qwen(api_key: str, model: str, base_url: str | None = None, **kwargs) -> Any:
    """Create a ChatTongyi via openai-compatible API."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=api_key or "sk-placeholder",
        model=model,
        base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=kwargs.get("temperature", 0.1),
        max_tokens=kwargs.get("max_tokens", 4096),
    )


def _create_moonshot(api_key: str, model: str, base_url: str | None = None, **kwargs) -> Any:
    """Create a ChatMoonshot via openai-compatible API."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=api_key or "sk-placeholder",
        model=model,
        base_url=base_url or "https://api.moonshot.cn/v1",
        temperature=kwargs.get("temperature", 0.1),
        max_tokens=kwargs.get("max_tokens", 4096),
    )


# ── Registry ───────────────────────────────────────────────────────────

PROVIDER_FACTORIES = {
    "openai": _create_openai,
    "deepseek": _create_deepseek,
    "zhipu": _create_zhipu,
    "qwen": _create_qwen,
    "moonshot": _create_moonshot,
}


def list_providers() -> list[str]:
    """Return available provider names."""
    return list(PROVIDER_FACTORIES.keys())


def create_llm(
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    **kwargs,
) -> Any:
    """Create a LangChain ChatModel for the given provider.

    Args:
        provider: "openai", "deepseek", "zhipu", "qwen", "moonshot"
        api_key: API key
        model: Model name (e.g. "gpt-4o", "deepseek-chat")
        base_url: Optional custom API base URL
        **kwargs: temperature, max_tokens, etc.

    Returns:
        A LangChain BaseChatModel instance.

    Raises:
        ValueError: Unknown provider
    """
    factory = PROVIDER_FACTORIES.get(provider)
    if factory is None:
        raise ValueError(f"Unknown provider: {provider}. Available: {list_providers()}")
    return factory(api_key=api_key, model=model, base_url=base_url, **kwargs)


# ── Connection test ────────────────────────────────────────────────────


# Minimal test message — costs ~1 token, enough to validate API key
_TEST_MESSAGE = "Hi"


def test_connection(
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Test whether the provider API key is valid by making a real API call.

    Sends a minimal message ("Hi") to the model and checks for a valid response.
    This catches invalid API keys, network errors, rate limits, etc.

    Args:
        provider: Provider name ("deepseek", "openai", "zhipu", "qwen", "moonshot")
        api_key: API key to validate
        model: Model name to test with
        base_url: Optional custom API base URL
        timeout: Maximum wait time in seconds (default 15)

    Returns:
        (success: bool, message: str) — success=True if connection is valid,
        with a descriptive message for the user.

    Examples:
>>> test_connection("deepseek", "sk-valid-key", "deepseek-chat")
(True, "✅ DeepSeek 连接成功（deepseek-chat，响应正常）")

>>> test_connection("deepseek", "bad-key", "deepseek-chat")
(False, "❌ API Key 无效（401），请检查 Key 是否正确")
    """
    start_time = time.time()

    try:
        # 1. Create the LLM instance
        llm = create_llm(provider, api_key=api_key, model=model, base_url=base_url,
                         temperature=0.0, max_tokens=5)

        # 2. Send a real API request — this is where auth is actually checked
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=_TEST_MESSAGE)], timeout=timeout)

        elapsed = time.time() - start_time

        # 3. Verify we got a valid response
        if response is None:
            return False, "❌ 连接失败：未收到响应"

        content = getattr(response, "content", None)
        if content is None or str(content).strip() == "":
            return False, "❌ 连接失败：响应内容为空"

        return True, (
            f"✅ {provider.title()} 连接成功（{model}，{elapsed:.1f}s）"
        )

    except Exception as e:
        elapsed = time.time() - start_time
        error_str = str(e)

        # Classify errors for user-friendly messages
        if "401" in error_str or "Unauthorized" in error_str or "Invalid API Key" in error_str or "Incorrect API key" in error_str:
            return False, "❌ API Key 无效（401），请检查 Key 是否正确"
        elif "403" in error_str or "Forbidden" in error_str:
            return False, "❌ 无权访问（403），请检查账户余额或权限"
        elif "429" in error_str or "Rate limit" in error_str or "Too Many Requests" in error_str:
            return False, "⏳ 请求过于频繁（429），请稍后再试"
        elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
            return False, f"⏱ 连接超时（{elapsed:.0f}s），请检查网络或稍后重试"
        elif "Connection" in error_str or "connect" in error_str.lower() or "Network" in error_str:
            return False, "🌐 网络连接失败，请检查网络环境"
        else:
            # Unknown error — include the raw message for debugging
            logger.warning("Connection test failed for %s: %s", provider, error_str)
            return False, f"❌ 连接失败：{error_str[:120]}"
