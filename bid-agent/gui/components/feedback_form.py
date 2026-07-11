"""反馈表单组件."""

import json

import streamlit as st


def render_feedback_form(result: dict):
    """Render a feedback submission form for manual user feedback."""
    st.markdown("**💬 提交反馈**")

    sections = result.get("sections", {})
    if not sections:
        st.caption("暂无可反馈的章节")
        return

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

    if st.button("📤 提交反馈", use_container_width=True):
        if not feedback_text.strip():
            st.warning("请输入反馈内容")
        else:
            # Save to feedback_history
            from datetime import datetime, timezone

            fb_entry = {
                "round": result.get("current_round", 0) + 1,
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

            st.success("✅ 反馈已提交，请在「生成与审阅」标签页查看")
            st.info("提示：点击「重新运行质量检查」后将应用反馈优化")
