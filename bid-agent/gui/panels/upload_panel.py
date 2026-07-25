"""上传面板 — 文件上传 + 标书类型选择 + 项目信息 + 启动生成."""

import tempfile
from pathlib import Path

import streamlit as st


def render_upload_panel():
    st.subheader("📤 上传招标文件")

    # Bid type selector
    bid_type = st.selectbox(
        "标书类型",
        # N08 fix: removed "软件类" (no knowledge_base directory exists),
        # N12 fix: changed default from index=7 to index=0 ("服务类")
        ["服务类", "货物类", "工程类", "集成类", "运维类", "劳务管理服务类", "劳务外包类"],
        index=0,
        key="bid_type",
    )

    # ── 重置按钮：当已有生成结果时显示，允许用户清空状态重新开始 ──────
    if (
        st.session_state.get("pipeline_result")
        or st.session_state.get("extraction_started")
        or st.session_state.get("start_generation")
    ):
        col_reset, col_status = st.columns([1, 3])
        with col_reset:
            if st.button("🔄 重置 / 重新开始", use_container_width=True, key="reset_btn_upload"):
                from gui.components.reset import reset_pipeline_state
                reset_pipeline_state()
                st.rerun()
        with col_status:
            st.caption("💡 点击重置可清空当前生成结果，重新上传文件并生成")

    # File uploader
    uploaded_files = st.file_uploader(
        "上传招标文件（PDF / DOCX / DOC）",
        type=["pdf", "docx", "doc"],
        accept_multiple_files=True,
        key="uploaded_files_uploader",
        help="支持同时上传多份招标文件，系统会自动解析并合并。支持 PDF、DOCX、DOC 格式",
    )

    if uploaded_files:
        st.success(f"已上传 {len(uploaded_files)} 个文件")
        for f in uploaded_files:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.text(f"📄 {f.name} ({f.size / 1024:.1f} KB)")
            with col2:
                st.caption(f"类型: {f.type}")

    # ── 投标人名称（必填）────────────────────────────────────────────
    st.divider()
    st.markdown("**投标人名称 *（必填）**")
    bidder_name = st.text_input(
        "投标人名称",
        key="form_bidder_name",
        help="投标公司全称，将作为「项目上下文契约」注入每章生成 prompt，确保全篇一致。此项为必填，未填写无法启动。",
        placeholder="如：广州华南人力有限公司",
    )
    if not bidder_name.strip():
        st.warning("⚠️ 投标人名称为必填项，未填写将无法启动解析。")

    # ── 项目其他信息（选填，用于上下文契约，保障全篇一致性）──────────────────
    with st.expander("📋 项目其他信息（选填，用于全篇一致性保障）", expanded=False):
        st.caption(
            "填写以下信息后，系统会将其作为「项目上下文契约」注入每章生成 prompt，"
            "确保招标人、项目地点等在全篇 8 章中完全一致。"
            "未填写的字段将由系统从招标文件和补充说明中自动提取。"
        )
        col1, col2 = st.columns(2)
        with col1:
            st.text_input(
                "招标人名称",
                key="form_tenderer_name",
                placeholder="如：XX大学",
            )
        with col2:
            st.text_input(
                "项目地点",
                key="form_project_location",
                placeholder="如：广州",
            )
            st.text_input(
                "工期/服务期",
                key="form_duration",
                placeholder="如：12个月",
            )

    # ── 补充说明 ────────────────────────────────────────────────────────
    st.divider()
    st.markdown("**补充要求**（选填）")
    extra_reqs = st.text_area(
        "补充说明",
        placeholder=(
            "例如：投标公司为广州华南人力有限公司，项目在广州，工期12个月，"
            "重点突出本地化服务能力、强调信息化管理平台...\n\n"
            "系统会调用大模型从补充说明中提取项目信息（投标人/地点/工期等）"
            "纳入上下文契约，并将策略性要求改写后注入各章节。"
        ),
        height=120,
        key="extra_reqs",
    )

    # Generation controls
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        max_rounds = st.number_input("最大优化轮次", 1, 5, 3, key="max_rounds")
    with col2:
        chunk_size = st.number_input("文档分块大小", 500, 2000, 1000, 100, key="chunk_size")
    with col3:
        st.markdown("<br>", unsafe_allow_html=True)
        start_disabled = not uploaded_files or not bidder_name.strip()
        if st.button(
            "📋 解析并提取信息",
            use_container_width=True,
            disabled=start_disabled,
            type="primary",
        ):
            with st.spinner("正在保存文件..."):
                # Save uploaded files to temp
                saved_files = []
                for uf in uploaded_files:
                    suffix = Path(uf.name).suffix
                    with tempfile.NamedTemporaryFile(
                        suffix=suffix, delete=False
                    ) as tmp:
                        tmp.write(uf.getvalue())
                        saved_files.append({
                            "filename": uf.name,
                            "path": tmp.name,
                            "type": suffix.lstrip("."),
                        })

                st.session_state["uploaded_docs"] = saved_files
                st.session_state["extraction_started"] = True

                st.success(f"✅ 准备就绪，共 {len(saved_files)} 个文件")
                st.info("👉 切换到「生成与审阅」标签页查看提取结果并校验")
