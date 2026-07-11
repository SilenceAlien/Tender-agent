"""信息校验表单组件 — 展示提取的关键信息供用户核对/修改.

US#6: 在文件上传后、标书生成前，展示从招标文件中提取的关键信息
(项目名称、招标编号、招标人、投标人等)，让用户确认无误后再开始生成。

展示内容：
1. 关键信息表格 — 每个字段可编辑，标注提取来源
2. 提取摘要统计 — 评分项/资质要求条数
3. 警告提示 — 必填字段缺失时标红
4. 确认按钮 — 点击后注入修正值并启动 Phase 2
"""

import streamlit as st


# ── 字段定义（展示顺序、标签、是否必填）──────────────────────────────────

_FIELDS = [
    ("project_name", "项目名称", True),
    ("bid_number", "招标编号", True),
    ("tenderer_name", "招标人", True),
    ("package_number", "包件号", False),
    ("bidder_name", "投标人", True),
    ("project_location", "项目地点", False),
    ("industry", "行业属性", False),
    ("duration", "工期/服务期", False),
    ("warranty", "质保期", False),
    ("service_target", "服务对象", False),
]

# 来源标签颜色映射
_SOURCE_COLORS = {
    "用户填写": "blue",
    "招标文件": "green",
    "补充说明": "orange",
    "缺失": "red",
}


def render_info_verification(extraction_result: dict) -> dict | None:
    """渲染信息校验表单，返回用户确认/修正后的字段 dict 或 None（未确认时）。

    Args:
        extraction_result: Phase 1 提取阶段的完整 AgentState，包含
            info_summary, requirements, project_contract 等。

    Returns:
        当用户点击「确认并开始生成」时，返回修正后的字段 dict；
        否则返回 None（用户尚未确认）。
    """
    info_summary = extraction_result.get("info_summary", {})
    if not info_summary:
        st.warning("未能提取到任何关键信息，请检查文件是否正确上传。")
        return None

    st.subheader("📋 关键信息校验")
    st.caption(
        "请核对以下从招标文件中自动提取的关键信息。"
        "确认无误后点击底部「确认并开始生成标书」按钮。"
        "如需修改，直接在对应输入框中编辑即可。"
    )

    # ── 提取摘要统计 ──────────────────────────────────────────────────
    scoring_count = info_summary.get("scoring_count", 0)
    qual_count = info_summary.get("qualification_count", 0)
    field_sources = info_summary.get("field_sources", {})

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("评分项", f"{scoring_count} 条")
    with col2:
        st.metric("资质要求", f"{qual_count} 条")
    with col3:
        bid_type = info_summary.get("bid_type", "未选择")
        st.metric("标书类型", bid_type)

    st.divider()

    # ── 关键信息表单 ──────────────────────────────────────────────────
    st.markdown("#### 关键信息")

    corrected_fields: dict[str, str] = {}
    missing_required: list[str] = []

    # 使用两列布局展示字段（每行两个字段）
    for i in range(0, len(_FIELDS), 2):
        col_left, col_right = st.columns(2)

        for j, col in enumerate([col_left, col_right]):
            if i + j >= len(_FIELDS):
                break
            field_key, label, required = _FIELDS[i + j]
            current_value = info_summary.get(field_key, "")
            source = field_sources.get(field_key, "缺失")

            with col:
                # 来源标签
                color = _SOURCE_COLORS.get(source, "gray")
                suffix = " *" if required else ""
                st.caption(f"{label}{suffix}")
                st.caption(f"来源: :{color}[**{source}**]")

                # 输入框
                edited_value = st.text_input(
                    label,
                    value=current_value,
                    key=f"verify_{field_key}",
                    label_visibility="collapsed",
                    placeholder=f"请输入{label}" if not current_value else "",
                )

                # 记录所有字段（包括被用户清空的）
                # 清空的字段记录为空字符串，merge 逻辑会据此尊重用户意图
                corrected_fields[field_key] = edited_value

                # 检查必填字段
                if required and not edited_value:
                    missing_required.append(label)

    # ── 警告提示 ──────────────────────────────────────────────────────
    if missing_required:
        st.warning(
            f"⚠️ 以下必填字段为空：{', '.join(missing_required)}。"
            f"请填写后再确认。"
        )

    st.divider()

    # ── 确认按钮 ──────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.caption("💡 确认后系统将使用以上信息生成标书全文。")
    with col3:
        confirm_disabled = bool(missing_required)
        if st.button(
            "✅ 确认无误，开始生成标书",
            use_container_width=True,
            disabled=confirm_disabled,
            type="primary",
            key="confirm_info_verification",
        ):
            # 返回所有字段（包括空值，merge 逻辑会区分「用户清空」和「未提供」）
            return corrected_fields

    return None
