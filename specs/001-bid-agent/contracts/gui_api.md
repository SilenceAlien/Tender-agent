# GUI 面板接口契约

## 面板状态管理

```python
# gui/app.py
import streamlit as st

# 全局 session state 约定
st.session_state.setdefault("active_panel", "config")  # config/upload/review/export
st.session_state.setdefault("project_id", None)         # SQLite project UUID
st.session_state.setdefault("api_keys", {})             # {provider_name: api_key_str}
st.session_state.setdefault("llm_config", {})           # {default_provider, default_model, task_routing}
st.session_state.setdefault("bid_type", None)           # service/goods/software/engineering/integration/ops
```

## 面板与核心引擎的接口

### 配置面板 → ModelRouter

```python
def set_api_key(provider: str, key: str) -> None
    """设置 LLM 供应商 API Key（仅存 session_state，不持久化）"""

def test_connection(provider: str, model: str) -> dict
    """测试连接 {success: bool, latency_ms: int, error: str|None}"""

def set_task_routing(task: str, provider: str, model: str) -> None
    """配置任务级模型覆盖"""
```

### 上传面板 → DocumentParser

```python
def upload_files(files: list[UploadedFile], project_id: str) -> dict
    """上传文件 {success: bool, parsed_count: int, errors: list[str]}"""

def start_extraction(project_id: str) -> str
    """US#6: 启动 Phase 1 提取子图，返回初始 status。
    运行 build_extraction_graph()，解析文档 + 提取关键信息。
    完成后设置 info_verification_status='pending'，等待用户校验。"""
```

### 校验面板 → InfoVerificationGate (US#6)

```python
def get_extraction_result() -> dict
    """获取 Phase 1 提取结果，包含 info_summary 供前端展示。"""

def confirm_info(corrected_fields: dict) -> dict
    """US#6: 用户校验后调用，注入修正值并启动 Phase 2。
    - 调用 apply_user_corrections(state, corrected_fields)
    - 设置 info_verification_status='confirmed'
    - 启动 build_generation_graph() 生成标书全文"""
```

### 审阅面板 → SectionGenerator / FeedbackProcessor

```python
def get_sections(project_id: str) -> dict[str, str]
    """获取当前所有章节 {section_name: content}"""

def get_progress(project_id: str) -> dict
    """获取生成进度 {current_node: str, completed_nodes: list[str], percent: int}"""

def submit_feedback(
    project_id: str, feedback_text: str, target_section: str,
    target_paragraph_index: int, feedback_type: str
) -> dict
    """提交反馈 {success: bool, round: int, message: str}"""
```

### 导出面板 → DocumentAssembler

```python
def get_quality_report(project_id: str) -> dict
    """获取质量报告"""

def export_docx(project_id: str, round: int) -> bytes
    """导出 DOCX 文件字节流"""

def get_version_history(project_id: str) -> list[dict]
    """获取版本历史 [{round: int, sections_count: int, word_count: int, date: str}, ...]"""
```

## Streamlit 路由逻辑

```python
# gui/app.py 路由伪代码
with st.sidebar:
    if st.button("模型配置"): st.session_state.active_panel = "config"
    if st.button("资料上传"): st.session_state.active_panel = "upload"
    if st.button("生成审阅"): st.session_state.active_panel = "review"
    if st.button("导出交付"): st.session_state.active_panel = "export"

if st.session_state.active_panel == "config":
    render_config_panel()
elif st.session_state.active_panel == "upload":
    render_upload_panel()
elif st.session_state.active_panel == "review":
    render_review_panel()
elif st.session_state.active_panel == "export":
    render_export_panel()
```
