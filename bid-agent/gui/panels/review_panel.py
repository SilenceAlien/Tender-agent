"""审阅面板 — 生成进度 + 章节切换 + 反馈提交."""

import streamlit as st

from gui.components.progress import render_pipeline_progress
from gui.components.feedback_form import render_feedback_form


def _sort_section_names(names: list[str], bid_type: str) -> list[str]:
    """Sort section names by their defined order.

    Uses the same section definition as section_generator to ensure
    consistent ordering regardless of dict insertion order.
    """
    try:
        from core.nodes.section_generator import _get_sections_for_bid_type
        defined = _get_sections_for_bid_type(bid_type)
        order_map = {s["name"]: i for i, s in enumerate(defined)}
        # Sort by defined order; unknown sections go to the end
        return sorted(names, key=lambda n: order_map.get(n, len(defined)))
    except Exception:
        return names


def _render_section_content(content: str) -> None:
    """Render section content with Mermaid diagrams and highlighted placeholders.

    Splits content into segments:
    - Mermaid code blocks (```mermaid ... ```) → rendered as SVG diagrams
    - Normal text → rendered as Markdown with red-bold placeholders
    """
    import re

    # Regex to find mermaid code blocks
    mermaid_re = re.compile(r"```mermaid\s*\n([\s\S]*?)```", re.MULTILINE)

    # Split content into alternating segments: text and mermaid blocks
    pos = 0
    for m in mermaid_re.finditer(content):
        # Text before the mermaid block
        if m.start() > pos:
            text_before = content[pos:m.start()].strip()
            if text_before:
                _render_text_with_placeholders(text_before)

        # Mermaid block
        mermaid_code = m.group(1).strip()
        _render_mermaid_diagram(mermaid_code)
        pos = m.end()

    # Remaining text after last mermaid block
    if pos < len(content):
        text_after = content[pos:].strip()
        if text_after:
            _render_text_with_placeholders(text_after)


def _render_text_with_placeholders(text: str) -> None:
    """Render text as Markdown with 【待填写：...】 highlighted in red bold."""
    import re

    def _highlight_placeholders(t: str) -> str:
        return re.sub(
            r"(【待填写[^】]*】)",
            r'<span style="color: red; font-weight: bold;">\1</span>',
            t,
        )

    st.markdown(_highlight_placeholders(text), unsafe_allow_html=True)


def _render_mermaid_diagram(mermaid_code: str) -> None:
    """Render a Mermaid diagram using Mermaid.js via HTML injection."""
    import html
    import streamlit.components.v1 as components

    escaped_code = html.escape(mermaid_code)
    mermaid_html = f"""
    <div style="display: flex; justify-content: center; margin: 16px 0;">
        <div class="mermaid-container" style="max-width: 100%; overflow-x: auto;">
            <div class="mermaid">{escaped_code}</div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        if (typeof mermaid !== 'undefined') {{
            mermaid.initialize({{
                startOnLoad: true,
                theme: 'default',
                flowchart: {{ useMaxWidth: true, htmlLabels: true, curve: 'basis' }},
                securityLevel: 'loose'
            }});
        }}
    </script>
    <style>
        .mermaid {{
            font-family: "SimHei", "Heiti SC", sans-serif;
            font-size: 14px;
        }}
        .mermaid .nodeLabel {{
            color: #333;
            font-weight: 500;
        }}
        .mermaid .edgeLabel {{
            background-color: #fff;
            padding: 2px 4px;
            border-radius: 3px;
        }}
    </style>
    """
    components.html(mermaid_html, height=400, scrolling=True)


def _build_form_fields() -> dict:
    """Collect form fields from the upload panel session state.

    These are passed as initial user_confirmed_fields so ContractExtractor
    can use them as the highest-priority source during extraction.
    """
    fields = {}
    for session_key, field_key in [
        ("form_bidder_name", "bidder_name"),
        ("form_tenderer_name", "tenderer_name"),
        ("form_project_location", "project_location"),
        ("form_duration", "duration"),
    ]:
        val = st.session_state.get(session_key, "")
        if val:
            fields[field_key] = val
    return fields


def render_review_panel():
    st.subheader("📝 生成与审阅")

    # Check if extraction was triggered (US#6: replaces start_generation)
    if not st.session_state.get("extraction_started"):
        st.info("👈 请先在「资料上传」标签页上传文件并点击「解析并提取信息」")
        return

    # ── 重置按钮：在管道运行前或运行后都可用 ──────────────────────────
    col_reset, col_spacer = st.columns([1, 3])
    with col_reset:
        if st.button("🔄 重置 / 重新开始", use_container_width=True, key="reset_btn_review"):
            from gui.components.reset import reset_pipeline_state
            reset_pipeline_state()
            st.rerun()
    with col_spacer:
        st.caption("💡 点击重置可清空当前生成结果，回到「资料上传」重新开始")

    # ════════════════════════════════════════════════════════════════════
    # Phase 1: Extraction — parse documents + extract key info (US#6)
    # ════════════════════════════════════════════════════════════════════
    if "extraction_result" not in st.session_state:
        with st.spinner("📋 正在解析文档并提取关键信息..."):
            from core.graph import build_extraction_graph
            from core.state import factory_state
            from gui.pipeline_runner import build_llm_for_pipeline

            docs = st.session_state.get("uploaded_docs", [])
            form_fields = _build_form_fields()

            state = factory_state(
                documents=docs,
                bid_type=st.session_state.get("bid_type", ""),
                extra_reqs=st.session_state.get("extra_reqs", ""),
                max_rounds=st.session_state.get("max_rounds", 3),
                chunk_size=st.session_state.get("chunk_size", 1000),
                user_confirmed_fields=form_fields,
            )

            # Load configured LLM functions (with pre-flight key validation)
            try:
                llm_fns = build_llm_for_pipeline(validate_keys=True)
            except ConnectionError as e:
                st.error(f"❌ API Key 验证失败：{e}")
                st.info("请先在「模型配置」标签页配置正确的 API Key 并点击「测试」验证通过")
                st.stop()

            # Wire ContractExtractor to use the same LLM as ReqExtractor
            if "ReqExtractor" in llm_fns and llm_fns["ReqExtractor"]:
                llm_fns["ContractExtractor"] = llm_fns["ReqExtractor"]

            # Store llm_fns for Phase 2 reuse
            st.session_state["pipeline_llm_fns"] = llm_fns

            extraction_graph = build_extraction_graph(llm_fns=llm_fns)
            extraction_result = extraction_graph.invoke(state)
            st.session_state["extraction_result"] = extraction_result

    extraction_result = st.session_state.get("extraction_result", {})

    # ── Check for early termination (DocumentParser/ReqExtractor failed) ──
    node_status = extraction_result.get("node_status", {})
    if node_status.get("DocumentParser") == "failed":
        st.error("❌ 文档解析失败，请检查文件格式是否正确")
        st.info("支持 PDF、DOCX、DOC 格式。请返回「资料上传」重新上传。")
        return
    if node_status.get("ReqExtractor") == "failed":
        st.error("❌ 需求提取失败，可能是文件内容为空或格式不支持")
        return

    # ── Show verification form if pending ───────────────────────────────
    if extraction_result.get("info_verification_status") == "pending":
        from gui.components.info_verification import render_info_verification
        from core.graph import apply_user_corrections

        corrected = render_info_verification(extraction_result)
        if corrected is not None:
            # User confirmed — apply corrections and start Phase 2
            with st.spinner("✅ 正在应用校验结果..."):
                correction_result = apply_user_corrections(extraction_result, corrected)
                # Merge corrections into extraction_result for Phase 2
                st.session_state["extraction_result"] = {
                    **extraction_result,
                    **correction_result,
                }
                st.session_state["generation_started"] = True
                st.rerun()
        return  # Stop here — wait for user to confirm

    # ════════════════════════════════════════════════════════════════════
    # Phase 2: Generation — generate sections + quality check + assembly
    # ════════════════════════════════════════════════════════════════════
    if st.session_state.get("generation_started") and "pipeline_result" not in st.session_state:
        with st.spinner("🚀 正在运行 AI 标书生成管道..."):
            from core.graph import build_generation_graph

            llm_fns = st.session_state.get("pipeline_llm_fns", {})
            generation_graph = build_generation_graph(llm_fns=llm_fns)

            # Use the confirmed extraction result as Phase 2 input state
            phase2_state = {**extraction_result}
            # Pre-set review_status for headless auto-approval in case
            # HumanReviewGate doesn't pause (mock mode)
            if not phase2_state.get("review_status"):
                phase2_state["review_status"] = "pending"

            result = generation_graph.invoke(phase2_state)
            st.session_state["pipeline_result"] = result
            st.session_state["pipeline_done"] = True

    result = st.session_state.get("pipeline_result")
    if not result:
        st.info("⏳ 等待生成...")
        return

    # Progress bars
    st.markdown("### 🔄 管道执行状态")
    render_pipeline_progress(result)

    # Section viewer
    st.divider()
    st.markdown("### 📑 章节内容")

    sections = result.get("sections", {})
    if not sections:
        st.warning("尚未生成任何章节")
        return

    # Sort sections by defined order (defensive — section_generator already
    # returns ordered dicts, but user edits may have reshuffled them)
    section_names = _sort_section_names(list(sections.keys()), result.get("bid_type", ""))
    selected = st.selectbox("选择章节", section_names, key="section_selector")

    if selected:
        content = sections.get(selected, "")
        # ── Rendered preview (Mermaid diagrams + highlighted placeholders) ──
        st.caption(f"预览「{selected}」")
        with st.container(border=True):
            if not content.strip():
                st.info("暂无内容")
            else:
                _render_section_content(content)

        # ── Editable raw content (collapsed by default) ─────────────────
        with st.expander("✏️ 编辑原始内容", expanded=False):
            edited = st.text_area(
                f"编辑「{selected}」",
                value=content,
                height=300,
                key=f"edit_{selected}",
                label_visibility="collapsed",
            )
            if edited != content:
                st.session_state["pipeline_result"]["sections"][selected] = edited
                st.rerun()

    # Quality report
    st.divider()
    st.markdown("### 📊 质量报告")
    quality = result.get("quality_report", {})
    col1, col2 = st.columns(2)
    with col1:
        verdict = quality.get("verdict", "N/A")
        color = "green" if verdict == "PASS" else "red"
        st.markdown(f"**评审结果**: :{color}[**{verdict}**]")
    with col2:
        total = quality.get("total_items", 0)
        passed = quality.get("passed_items", 0)
        st.metric("通过率", f"{passed}/{total}" if total else "N/A")

    if quality.get("compliance"):
        st.markdown("**合规问题**:")
        for issue in quality.get("compliance", [])[:10]:
            st.markdown(f"- ⚠️ {issue}")

    if quality.get("consistency"):
        st.markdown("**一致性问题**:")
        for issue in quality.get("consistency", []):
            # Consistency issues may contain multi-line detail reports
            # Display each as a code block for readability
            if "\n" in issue:
                st.warning(issue)
            else:
                st.markdown(f"- ⚠️ {issue}")

    # ── R02 fix: 人工审核裁决 ──────────────────────────────────────────
    # Previously the pipeline paused at HumanReviewGate (pending) but
    # review_panel never called resume_after_review(), making the
    # approved→DocumentAssembler and rejected→FeedbackProcessor routes
    # dead code.  Now we provide explicit approve/reject buttons.
    review_status = result.get("review_status", "")
    if review_status == "pending" or (not review_status and sections):
        st.divider()
        st.markdown("### ✅ 人工审核")
        st.caption("审核通过后将自动生成 DOCX 文档；驳回将触发修改循环。")

        col_approve, col_reject = st.columns(2)

        with col_approve:
            if st.button("✅ 通过审核", use_container_width=True, type="primary"):
                from core.nodes.human_review_gate import resume_after_review

                # Apply approved verdict to state
                verdict_update = resume_after_review(result, "approved")
                result.update(verdict_update)
                result["review_status"] = "approved"

                # Run DocumentAssembler to generate DOCX
                with st.spinner("📄 正在生成 DOCX 文档..."):
                    from pathlib import Path

                    from core.nodes.doc_assembler import doc_assembler

                    # Use persistent directory so export_path stays valid
                    export_dir = Path.home() / ".bid-agent" / "exports"
                    export_dir.mkdir(parents=True, exist_ok=True)
                    assembly = doc_assembler(result, export_dir=str(export_dir))
                    export_path = assembly.get("export_path", "")
                    if export_path and Path(export_path).exists():
                        # Store for export panel download
                        st.session_state["approved_export_path"] = export_path
                        result["export_path"] = export_path
                        with open(export_path, "rb") as f:
                            st.session_state["approved_docx_bytes"] = f.read()
                        st.success("✅ 审核通过！DOCX 文档已生成。")
                        st.info("📥 请前往「导出交付」标签页下载文档")
                    else:
                        st.error("文档生成失败，请检查日志")

                st.session_state["pipeline_result"] = result
                st.rerun()

        with col_reject:
            if st.button("❌ 驳回修改", use_container_width=True):
                st.session_state["show_reject_form"] = True

        # Rejection form (expands when "驳回修改" is clicked)
        if st.session_state.get("show_reject_form"):
            with st.container(border=True):
                st.markdown("**驳回意见**")
                reject_comments = st.text_area(
                    "请输入修改意见（将作为反馈传给生成器）",
                    height=100,
                    key="reject_comments_input",
                )
                col_confirm, col_cancel = st.columns(2)
                with col_confirm:
                    if st.button("确认驳回", use_container_width=True, type="primary"):
                        if not reject_comments.strip():
                            st.warning("请输入驳回意见")
                        else:
                            from core.nodes.human_review_gate import resume_after_review

                            # Apply rejected verdict with comments
                            comments_list = [c.strip() for c in reject_comments.split("\n") if c.strip()]
                            verdict_update = resume_after_review(result, "rejected", comments_list)
                            result.update(verdict_update)
                            result["review_status"] = "rejected"
                            st.session_state["pipeline_result"] = result
                            st.session_state["show_reject_form"] = False

                            # Re-invoke generation graph to trigger feedback loop
                            with st.spinner("🔄 正在根据审核意见重新生成..."):
                                from core.graph import build_generation_graph

                                llm_fns = st.session_state.get("pipeline_llm_fns", {})
                                generation_graph = build_generation_graph(llm_fns=llm_fns)
                                phase2_state = {**result}
                                # Clear review_status so HumanReviewGate returns
                                # "pending" after revision, not "rejected" again.
                                # If we don't clear it, HumanReviewGate sees the
                                # stale "rejected" verdict and loops until max_rounds.
                                phase2_state["review_status"] = ""
                                new_result = generation_graph.invoke(phase2_state)
                                st.session_state["pipeline_result"] = new_result

                            st.success("✅ 已根据审核意见重新生成，请审阅更新后的内容")
                            st.rerun()

                with col_cancel:
                    if st.button("取消", use_container_width=True):
                        st.session_state["show_reject_form"] = False
                        st.rerun()

    elif review_status == "approved":
        st.divider()
        st.markdown("### ✅ 人工审核 — 已通过")
        docx_bytes = st.session_state.get("approved_docx_bytes")
        if docx_bytes:
            st.download_button(
                "⬇️ 下载 DOCX 文件",
                docx_bytes,
                file_name="标书.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        else:
            st.info("📥 请前往「导出交付」标签页下载文档")

    # Feedback form
    st.divider()
    render_feedback_form(result)

    # Version history
    feedback_history = result.get("feedback_history", [])
    if feedback_history:
        st.divider()
        st.markdown("### 📜 修改历史")
        for i, fb in enumerate(reversed(feedback_history[-10:]), 1):
            with st.expander(
                f"第{fb.get('round', '?')}轮 — {fb.get('feedback_type', '未知')}"
            ):
                st.text(fb.get("feedback_text", ""))
                st.caption(f"时间: {fb.get('timestamp', 'N/A')}")

    # ── Pipeline stage snapshots ────────────────────────────────────────
    snapshots = st.session_state.get("pipeline_snapshots", [])
    if snapshots:
        st.divider()
        st.markdown("### 📸 管道阶段快照")
        st.caption("每个阶段执行完毕后的完整状态已导出为 JSON 文件：")

        for path in snapshots:
            from pathlib import Path as _Path
            p = _Path(path)
            # Parse label from filename: YYYYMMDD_HHMMSS_01_DocumentParser.json
            parts = p.stem.split("_", 3)
            label = parts[-1] if len(parts) >= 3 else p.name
            st.code(f"  {p.name}  —  {label}", language=None)

        if snapshots:
            st.info(f"📁 导出目录：{_Path(snapshots[0]).parent}")
