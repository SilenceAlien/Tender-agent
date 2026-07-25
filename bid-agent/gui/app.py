"""Streamlit main app — 四面板 GUI for 标书制作智能体.

Panel 1: 模型配置 (config_panel)
Panel 2: 资料上传 (upload_panel)
Panel 3: 生成与审阅 (review_panel)
Panel 4: 导出交付 (export_panel)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Configure file logging (idempotent — survives Streamlit reruns) ─────
# All loggers (core.*, gui.*) write to ~/.bid-agent/logs/bid-agent.log
# so users can inspect errors that aren't visible in the Streamlit UI.
#
# Streamlit reruns the entire script on every interaction, so we guard
# against duplicate handler registration with a module-level flag.
_log_dir = Path.home() / ".bid-agent" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "bid-agent.log"

_root_logger = logging.getLogger()

if not getattr(_root_logger, "_bid_agent_configured", False):
    _file_handler = logging.FileHandler(_log_file, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    _console_handler = logging.StreamHandler()
    _console_handler.setLevel(logging.INFO)
    _console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    _root_logger.setLevel(logging.DEBUG)
    _root_logger.addHandler(_file_handler)
    _root_logger.addHandler(_console_handler)
    _root_logger._bid_agent_configured = True

    logging.info(f"=== Bid Agent started at {datetime.now().isoformat()} ===")
    logging.info(f"Log file: {_log_file}")

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
