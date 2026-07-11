"""Streamlit main app — 四面板 GUI for 标书制作智能体.

Panel 1: 模型配置 (config_panel)
Panel 2: 资料上传 (upload_panel)
Panel 3: 生成与审阅 (review_panel)
Panel 4: 导出交付 (export_panel)
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st


def main():
    st.set_page_config(
        page_title="标书制作智能体",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("📋 标书制作智能体")
    st.caption("上传招标文件 → 解析提取 → 信息校验 → 生成标书 → 审阅优化 → 导出交付")

    # Import panels
    from gui.panels.config_panel import render_config_panel
    from gui.panels.upload_panel import render_upload_panel
    from gui.panels.review_panel import render_review_panel
    from gui.panels.export_panel import render_export_panel

    # Tab routing
    tab1, tab2, tab3, tab4 = st.tabs(
        ["⚙️ 模型配置", "📤 资料上传", "📝 生成与审阅", "💾 导出交付"]
    )

    with tab1:
        render_config_panel()

    with tab2:
        render_upload_panel()

    with tab3:
        render_review_panel()

    with tab4:
        render_export_panel()


if __name__ == "__main__":
    main()
