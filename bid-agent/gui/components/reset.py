"""共享重置功能 — 清除管道状态，允许用户重新上传资料并再次生成.

清除范围（管道生成相关）：
    - uploaded_docs          临时文件路径列表
    - start_generation       生成启动标志
    - pipeline_result        管道执行结果
    - pipeline_done          管道完成标志
    - pipeline_snapshots     快照文件路径列表
    - section_selector       章节选择器
    - edit_<section_name>    各章节编辑框
    - uploaded_files_uploader file_uploader 控件本身（清空已选文件）

保留范围（用户配置，避免重复填写）：
    - bid_type / extra_reqs / max_rounds / chunk_size
    - form_bidder_name / form_tenderer_name / form_project_location / form_duration
    - config_*               模型配置面板的所有配置
"""

import logging
from pathlib import Path

import streamlit as st

logger = logging.getLogger(__name__)

# 所有管道生成相关的 session_state key
_PIPELINE_KEYS = [
    "uploaded_docs",
    "extraction_started",  # US#6: Phase 1 trigger
    "extraction_result",  # US#6: Phase 1 result
    "generation_started",  # US#6: Phase 2 trigger
    "pipeline_llm_fns",   # US#6: LLM fns shared between phases
    "start_generation",   # Legacy (backward compat)
    "pipeline_result",
    "pipeline_done",
    "pipeline_snapshots",
    "section_selector",
]

# file_uploader 的 key（需与 upload_panel.py 中一致）
_FILE_UPLOADER_KEY = "uploaded_files_uploader"


def reset_pipeline_state() -> None:
    """清除所有管道生成相关的 session_state，并清理临时文件。

    保留用户配置（标书类型、补充说明、模型配置等），仅重置生成流程状态。
    清除 file_uploader 控件使已上传文件被清空。
    """
    # ── 1. 清理上传的临时文件 ──────────────────────────────────────────
    docs = st.session_state.get("uploaded_docs", [])
    for doc in docs:
        path = doc.get("path", "")
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {path}: {e}")

    # ── 2. 删除管道相关的 session_state key ───────────────────────────
    for key in _PIPELINE_KEYS:
        st.session_state.pop(key, None)

    # ── 3. 删除各章节的编辑框 key (edit_<section_name>) 和校验表单 key (verify_<field>) ───
    keys_to_remove = [
        k for k in st.session_state
        if k.startswith("edit_") or k.startswith("verify_")
    ]
    for k in keys_to_remove:
        st.session_state.pop(k, None)

    # ── 4. 清除 file_uploader 控件（使已选文件被清空）─────────────────
    st.session_state.pop(_FILE_UPLOADER_KEY, None)

    logger.info("Pipeline state reset — all generation state cleared, temp files cleaned")
