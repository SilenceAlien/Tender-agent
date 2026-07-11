"""导出面板 — DOCX 下载 + 版本管理."""

import streamlit as st


def render_export_panel():
    st.subheader("💾 导出交付")

    if "pipeline_result" not in st.session_state:
        st.info("👈 请先在「资料上传」标签页生成标书")
        return

    # ── 重置按钮 ──────────────────────────────────────────────────────
    col_reset, col_spacer = st.columns([1, 3])
    with col_reset:
        if st.button("🔄 重置 / 重新开始", use_container_width=True, key="reset_btn_export"):
            from gui.components.reset import reset_pipeline_state
            reset_pipeline_state()
            st.rerun()
    with col_spacer:
        st.caption("💡 点击重置可清空当前生成结果，回到「资料上传」重新开始")
    st.divider()

    result = st.session_state["pipeline_result"]
    quality = result.get("quality_report", {})

    # Quality gate
    verdict = quality.get("verdict", "FAIL")
    if verdict != "PASS":
        st.warning("⚠️ 质量检查未通过，建议先审阅优化后再导出")
        if st.button("🔁 重新运行质量检查"):
            from core.nodes.quality_checker import quality_checker

            # BUG-15 fix: pass llm_fn so LLM-based deep checks also run,
            # not just local rule checks.
            llm_fn = None
            try:
                from core.graph import get_pipeline_llm
                llm_fn = get_pipeline_llm()
                if llm_fn is None:
                    # Try building from saved config (skip key validation
                    # to avoid ConnectionError in the export panel)
                    from gui.pipeline_runner import build_llm_for_pipeline
                    llm_fns = build_llm_for_pipeline(validate_keys=False)
                    llm_fn = llm_fns.get("QualityChecker")
            except Exception:
                pass  # Fall back to local-only checks if LLM unavailable

            qc = quality_checker(result, llm_fn=llm_fn)
            result.update(qc)
            st.session_state["pipeline_result"] = result
            st.rerun()

    # Export button
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📥 导出 DOCX", use_container_width=True):
            with st.spinner("正在生成 DOCX..."):
                import tempfile
                from pathlib import Path

                from core.nodes.doc_assembler import doc_assembler

                with tempfile.TemporaryDirectory() as tmpdir:
                    assembly = doc_assembler(result, export_dir=tmpdir)
                    export_path = assembly.get("export_path", "")
                    if export_path and Path(export_path).exists():
                        with open(export_path, "rb") as f:
                            st.download_button(
                                "⬇️ 下载 DOCX 文件",
                                f.read(),
                                file_name=Path(export_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            )
                        st.success(f"✅ 导出成功: {Path(export_path).name}")
                    else:
                        st.error("导出失败")

    with col2:
        if st.button("📋 导出 JSON", use_container_width=True):
            import json

            json_data = json.dumps(
                {"sections": result.get("sections", {})},
                ensure_ascii=False,
                indent=2,
            )
            st.download_button(
                "⬇️ 下载 JSON",
                json_data,
                file_name="标书内容.json",
                mime="application/json",
            )

    # Version history
    st.divider()
    st.markdown("### 📜 版本历史")

    feedback_history = result.get("feedback_history", [])
    rounds = sorted(set(fb.get("round", 0) for fb in feedback_history))

    if not rounds:
        st.info("暂无版本历史（初稿，未进行修改）")
    else:
        for r in rounds:
            round_feedbacks = [fb for fb in feedback_history if fb.get("round") == r]
            with st.expander(f"v{r} — {len(round_feedbacks)} 条反馈"):
                for fb in round_feedbacks:
                    st.markdown(f"- [{fb.get('feedback_type', '?')}] {fb.get('feedback_text', '')}")
