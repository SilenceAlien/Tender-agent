"""配置面板 — API Key 输入 + 模型选择 + 持久化到 ~/.bid-agent/config.json."""

import streamlit as st

from core.config_persistence import load_config, save_config

# ── DeepSeek 模型信息（来源: https://api-docs.deepseek.com/zh-cn/） ──

DS_MODELS = [
    {"id": "deepseek-v4-pro",   "label": "DeepSeek-V4-Pro（推荐·最强）", "help": "1M上下文 | 高性能推理 | ¥3/6 百万tokens"},
    {"id": "deepseek-v4-flash", "label": "DeepSeek-V4-Flash（快速·实惠）", "help": "1M上下文 | 支持思考/非思考 | ¥1/2 百万tokens"},
]

ALL_DS = [m["id"] for m in DS_MODELS]
ALL_DS_LABELS = {m["id"]: m["label"] for m in DS_MODELS}
ALL_DS_HELP = {m["id"]: m["help"] for m in DS_MODELS}

OA_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]


def _init_session_config():
    """Load config from disk → session_state on first render."""
    if "config_loaded" in st.session_state:
        return

    cfg = load_config()
    st.session_state["config_provider"] = cfg.get("provider", "deepseek")
    st.session_state["config_model"] = cfg.get("model", "deepseek-v4-flash")
    st.session_state["config_api_keys"] = cfg.get("api_keys", {})
    st.session_state["config_node_overrides"] = cfg.get("node_overrides", {})
    # BUG-12 fix: read openai_model from its own config field, not api_keys
    st.session_state["config_openai_model"] = cfg.get("openai_model", "gpt-4o-mini")
    st.session_state["config_loaded"] = True


def _save_to_disk():
    """Persist current session state to ~/.bid-agent/config.json."""
    save_config({
        "provider": st.session_state.get("config_provider", "deepseek"),
        "model": st.session_state.get("config_model", "deepseek-v4-flash"),
        "api_keys": st.session_state.get("config_api_keys", {}),
        "node_overrides": st.session_state.get("config_node_overrides", {}),
        # BUG-12 fix: store openai_model separately, not in api_keys
        "openai_model": st.session_state.get("config_openai_model", "gpt-4o-mini"),
    })


def render_config_panel():
    _init_session_config()

    st.subheader("⚙️ LLM 模型配置")

    # ── Status bar ──────────────────────────────────────────────────────
    cfg_provider = st.session_state.get("config_provider", "")
    cfg_model = st.session_state.get("config_model", "")
    api_keys = st.session_state.get("config_api_keys", {})
    has_ds = bool(api_keys.get("deepseek"))
    has_oa = bool(api_keys.get("openai"))

    status_parts = []
    if has_ds: status_parts.append("✅ DeepSeek")
    else:      status_parts.append("❌ DeepSeek 未配置")
    if has_oa: status_parts.append("✅ OpenAI")
    else:      status_parts.append("❌ OpenAI 未配置")
    st.caption(" | ".join(status_parts))

    col1, col2 = st.columns(2)

    # ── DeepSeek ────────────────────────────────────────────────────────
    with col1:
        st.markdown("**DeepSeek**")

        ds_key = st.text_input(
            "DeepSeek API Key",
            type="password",
            value=api_keys.get("deepseek", ""),
            key="ds_key",
            help="从 https://platform.deepseek.com/api_keys 获取",
        )

        # Default model index
        try:
            ds_idx = ALL_DS.index(cfg_model) if cfg_model in ALL_DS else 0
        except ValueError:
            ds_idx = 0

        ds_model = st.selectbox(
            "模型",
            options=ALL_DS,
            format_func=lambda x: ALL_DS_LABELS.get(x, x),
            index=ds_idx,
            key="ds_model",
        )
        st.caption(f"📌 {ALL_DS_HELP.get(ds_model, '')}")

    # ── OpenAI ──────────────────────────────────────────────────────────
    with col2:
        st.markdown("**OpenAI**")
        oa_key = st.text_input(
            "OpenAI API Key",
            type="password",
            value=api_keys.get("openai", ""),
            key="oa_key",
            help="从 https://platform.openai.com/api-keys 获取",
        )
        try:
            # BUG-12 fix: read openai_model from session_state, not api_keys
            oa_idx = OA_MODELS.index(st.session_state.get("config_openai_model", "gpt-4o-mini"))
        except ValueError:
            oa_idx = 0
        oa_model = st.selectbox("模型", OA_MODELS, index=oa_idx, key="oa_model")

    # ── Auto-save whenever keys change ──────────────────────────────────
    # Streamlit reruns on every interaction.  We sync keys to session_state
    # AND to disk immediately, so the pipeline runner (which reads from disk
    # via load_config()) always gets the latest keys.
    if ds_key:
        api_keys["deepseek"] = ds_key
    if oa_key:
        api_keys["openai"] = oa_key
    # BUG-12 fix: store oa_model in a separate config field, not in api_keys
    st.session_state["config_openai_model"] = oa_model

    st.session_state["config_api_keys"] = api_keys
    st.session_state["config_provider"] = "deepseek"
    st.session_state["config_model"] = ds_model

    # Persist to disk automatically — avoid "forgot to save" failures
    # when the user switches to the generation tab.
    _save_to_disk()

    # ── Buttons ─────────────────────────────────────────────────────────
    st.divider()
    col3, col4, col5 = st.columns(3)

    with col3:
        if st.button("🔄 测试 DeepSeek", use_container_width=True):
            if not ds_key:
                st.error("请先输入 API Key")
            elif not ds_key.strip():
                st.error("API Key 不能为空")
            else:
                with st.spinner("正在连接 DeepSeek API 验证密钥..."):
                    from core.llm.providers import test_connection
                    ok, msg = test_connection("deepseek", api_key=ds_key, model=ds_model)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

    with col4:
        if st.button("🔄 测试 OpenAI", use_container_width=True):
            if not oa_key:
                st.error("请先输入 API Key")
            elif not oa_key.strip():
                st.error("API Key 不能为空")
            else:
                with st.spinner("正在连接 OpenAI API 验证密钥..."):
                    from core.llm.providers import test_connection
                    ok, msg = test_connection("openai", api_key=oa_key, model=oa_model)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

    with col5:
        if st.button("💾 保存配置", use_container_width=True, type="primary"):
            _save_to_disk()
            st.success(f"✅ 已保存到 ~/.bid-agent/config.json")
            st.info("下次启动将自动加载此配置")
