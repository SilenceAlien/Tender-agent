"""管道进度可视化组件."""

import streamlit as st

ALL_NODES = [
    "DocumentParser",
    "ReqExtractor",
    "ContractExtractor",
    # M13 fix: InfoVerificationGate was missing from progress display
    "InfoVerificationGate",
    "EligibilityChecker",
    "TemplateMatcher",
    "SectionGenerator",
    "QualityChecker",
    "FeedbackProcessor",
    "CrossReferenceChecker",
    "ComplianceChecker",
    "ScoreSimulator",
    "HumanReviewGate",
    "DocumentAssembler",
]

NODE_LABELS = {
    "DocumentParser": "📄 文档解析",
    "ReqExtractor": "🔍 需求提取",
    "ContractExtractor": "📜 契约构建",
    # M13 fix: add label for InfoVerificationGate
    "InfoVerificationGate": "🔍 信息校验",
    "EligibilityChecker": "🔐 资质校验",
    "TemplateMatcher": "📋 模板匹配",
    "SectionGenerator": "✍️ 章节生成",
    "QualityChecker": "✅ 质量检查",
    "FeedbackProcessor": "🔄 反馈处理",
    "CrossReferenceChecker": "🔗 跨章校验",
    "ComplianceChecker": "⚖️ 合规审查",
    "ScoreSimulator": "📊 评分模拟",
    "HumanReviewGate": "👤 人工审核",
    "DocumentAssembler": "📦 文档装配",
}


def render_pipeline_progress(result: dict):
    """Render a visual pipeline progress indicator."""
    node_status = result.get("node_status", {})

    cols = st.columns(len(ALL_NODES))
    for i, node in enumerate(ALL_NODES):
        status = node_status.get(node, "pending")
        label = NODE_LABELS.get(node, node)

        with cols[i]:
            if status == "completed":
                st.success(f"{label}\n✅")
            elif status == "running":
                st.info(f"{label}\n🔄")
            elif status == "failed":
                st.error(f"{label}\n❌")
            else:
                st.caption(f"{label}\n⏳")
