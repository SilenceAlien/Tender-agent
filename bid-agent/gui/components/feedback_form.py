"""反馈表单组件.

提供两种反馈提交方式：
1. 单条反馈提交 — 保存到 feedback_history，用户可累积多条反馈后统一应用
2. 应用反馈并重新生成 — 将已提交的反馈注入管道，触发 SectionGenerator 定向修订
   → QualityChecker → CrossReferenceChecker → ComplianceChecker → ScoreSimulator
   → HumanReviewGate（暂停等待再次审核）

设计要点：
- 提交反馈时不立即触发管道，允许用户一次提交多条反馈后再统一应用
- 应用反馈时通过 build_generation_graph 重新执行生成阶段
- feedback_history 中 target_section 非空的条目为 local 反馈，仅重生成指定章节
- feedback_history 中 target_section 为空的条目为 global 反馈，重生成所有章节
- 受 max_rounds 限制，超过后强制通过（防止无限循环）
"""

import streamlit as st


def _apply_feedback_and_regenerate(result: dict) -> None:
    """Trigger pipeline re-execution with accumulated feedback_history.

    Mirrors the "驳回修改" path in review_panel.py:
    1. Clear review_status so HumanReviewGate returns "pending" after revision
    2. Re-invoke build_generation_graph with updated state
    3. Store result back to session state

    Note: current_round is NOT incremented here — it is managed exclusively
    by FeedbackProcessor when QualityChecker fails. This prevents double-
    incrementing (user button + FeedbackProcessor).

    The SectionGenerator's _collect_section_feedback() reads feedback_history
    and only regenerates sections that have feedback — other sections are
    preserved as-is (targeted revision).
    """
    with st.spinner("🔄 正在应用反馈并重新生成章节..."):
        from core.graph import build_generation_graph

        llm_fns = st.session_state.get("pipeline_llm_fns", {})
        generation_graph = build_generation_graph(llm_fns=llm_fns)

        phase2_state = {**result}
        # Clear review_status so HumanReviewGate returns "pending" after
        # revision, not a stale "rejected"/"approved" from a previous round.
        phase2_state["review_status"] = ""

        new_result = generation_graph.invoke(phase2_state)
        st.session_state["pipeline_result"] = new_result

    st.success("✅ 已应用反馈重新生成，请审阅更新后的内容")
    st.rerun()


def render_feedback_form(result: dict):
    """Render a feedback submission form for manual user feedback.

    Two-step workflow:
    1. User submits one or more feedback items (saved to feedback_history)
    2. User clicks "应用反馈并重新生成" to trigger pipeline re-execution

    The feedback items accumulate in feedback_history until the user chooses
    to apply them. This allows submitting multiple feedback items (e.g.,
    one for chapter 3 and one for chapter 5) before triggering a single
    regeneration cycle.
    """
    st.markdown("### 💬 提交反馈")

    sections = result.get("sections", {})
    if not sections:
        st.caption("暂无可反馈的章节")
        return

    current_round = result.get("current_round", 0)
    max_rounds = result.get("max_rounds", 3)

    # ── Show current round status ────────────────────────────────────
    pending_feedback = [
        fb for fb in result.get("feedback_history", [])
        if fb.get("round", 0) == current_round + 1
    ]
    if pending_feedback:
        st.info(
            f"📋 本轮已提交 {len(pending_feedback)} 条反馈，"
            f"点击下方「应用反馈并重新生成」按钮执行修订。"
        )

    # ── Feedback input form ───────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        feedback_type = st.selectbox(
            "反馈类型",
            ["content_fix", "style_adjust", "structure", "score_align"],
            format_func=lambda x: {
                "content_fix": "📝 内容修正",
                "style_adjust": "🎨 风格调整",
                "structure": "🏗️ 结构优化",
                "score_align": "📊 评分对齐",
            }.get(x, x),
            key="fb_type",
        )
    with col2:
        target_section = st.selectbox(
            "目标章节",
            list(sections.keys()),
            key="fb_section",
        )

    feedback_text = st.text_area(
        "反馈内容",
        placeholder="例如：请补充项目团队成员的证书编号和发证机关",
        key="fb_text",
    )

    # ── Submit feedback button (saves to feedback_history) ────────────
    if st.button("📤 提交反馈", use_container_width=True):
        if not feedback_text.strip():
            st.warning("请输入反馈内容")
        else:
            from datetime import datetime, timezone

            fb_entry = {
                "round": current_round + 1,
                "feedback_text": feedback_text,
                "feedback_type": feedback_type,
                "scope": "local",
                "target_section": target_section,
                "target_paragraph_index": -1,
                "diff_before": sections.get(target_section, ""),
                "diff_after": "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if "feedback_history" not in result:
                result["feedback_history"] = []
            result["feedback_history"].append(fb_entry)
            st.session_state["pipeline_result"] = result

            st.success(f"✅ 反馈已提交（目标：{target_section}）")
            st.rerun()

    # ── Apply feedback button (triggers pipeline re-execution) ───────
    has_pending = bool(pending_feedback)
    col_apply, col_round_info = st.columns([2, 1])

    with col_apply:
        apply_label = "🔄 应用反馈并重新生成" if has_pending else "🔄 应用反馈并重新生成（暂无反馈）"
        if st.button(
            apply_label,
            use_container_width=True,
            type="primary" if has_pending else "secondary",
            disabled=not has_pending,
        ):
            _apply_feedback_and_regenerate(result)

    with col_round_info:
        st.caption(f"当前轮次：{current_round}/{max_rounds}")
