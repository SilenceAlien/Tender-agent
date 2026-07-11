# 节点接口契约

## 通用约定

- 所有节点函数签名：`def node_fn(state: AgentState) -> dict`
- 返回值：`dict`，仅包含需要更新的 AgentState 字段（LangGraph 自动 merge）
- 错误处理：异常需捕获并写入 `node_status[node_name] = NodeStatus.FAILED`
- LLM 调用：通过 `ModelRouter.get_model(node_name, state)` 获取

## DocumentParser

```python
def document_parser(state: AgentState) -> dict:
    """
    输入：state.documents（文件列表）
    输出：{documents: [附加 parsed_content 的文档], node_status: {"DocumentParser": COMPLETED}}
    
    约束：
    - 每个文件 chunk_size=1000, chunk_overlap=200
    - 解析失败的文件：标记 status=FAILED 并记录错误原因
    - 至少成功解析 1 个文件才标记 COMPLETED
    """
```

## ReqExtractor

```python
def req_extractor(state: AgentState) -> dict:
    """
    输入：state.documents[].parsed_content
    输出：{requirements: {project_name, bid_number, tenderer_name, package_number,
                         scoring, qualifications, tech_specs, format_rules}, node_status: {...}}
    
    约束：
    - scoring 必须为 list[dict] 格式：每项含 {item_name, score, criteria}
    - qualifications 必须为 list[str]
    - format_rules 必须含 page_margin, font, line_spacing 等键（缺省则用默认值）
    - project_name, bid_number, tenderer_name, package_number 从招标文件中提取
    - 如 LLM 返回非 JSON 格式 → 重试 1 次 → 仍失败则标记 FAILED
    """
```

## ContractExtractor

```python
def contract_extractor(state: AgentState, llm_fn=None) -> dict:
    """
    输入：state.extra_reqs, state.requirements, state.bid_type, state.user_confirmed_fields
    输出：{project_contract: dict, global_constraints: [str], node_status}
    
    约束：
    - 四方合并优先级：用户校验确认 > 招标文件提取 > LLM从补充说明提取
    - user_confirmed_fields 来自 InfoVerificationGate 的用户校验结果
    - 构建 ProjectContextContract 并推断禁止项
    """
```

## InfoVerificationGate (US#6)

```python
def info_verification_gate(state: AgentState, auto_confirm=False) -> dict:
    """
    输入：state.requirements, state.project_contract, state.bid_type
    输出：{info_verification_status: "pending"|"confirmed",
           info_summary: {...}, node_status}
    
    约束：
    - 交互模式 (auto_confirm=False)：设置 pending 并返回，GUI 展示校验表单
    - 无头模式 (auto_confirm=True)：直接设置 confirmed
    - info_summary 包含所有关键字段值 + field_sources 来源标注
    - 用户确认后通过 apply_user_corrections() 注入修正值
    """

def apply_user_corrections(state: AgentState, corrected_fields: dict) -> dict:
    """
    输入：corrected_fields: 用户确认/修改后的字段
    输出：{info_verification_status: "confirmed",
           user_confirmed_fields: corrected_fields,
           project_contract: 重新合并后的 dict}
    
    约束：
    - 修正值作为最高优先级注入 project_contract
    - 调用 _merge_contract_sources(user_confirmed_fields=corrected_fields) 重新合并
    """
```

## TemplateMatcher

```python
def template_matcher(state: AgentState) -> dict:
    """
    输入：state.requirements
    输出：{matched_templates: [template_id, ...], node_status: {...}}
    
    约束：
    - 仅查模板库索引（Index 1, HNSWFlat）
    - 返回 top-3 模板 ID，按余弦相似度降序
    - 如无模板命中（相似度 < 0.3）→ 返回空列表但标记 COMPLETED（提示手动选模板）
    """
```

## SectionGenerator

```python
def section_generator(state: AgentState) -> dict:
    """
    输入：state.requirements + state.matched_templates + state.selected_template_id
          + state.global_constraints + state.compressed_history + state.feedback_history
    输出：{sections: {section_name: content, ...}, current_section: "", node_status: {...}}
    
    约束：
    - Prompt 按三层上下文组装：
      Layer 1: global_constraints（~200 tokens，硬注入 system prompt，不可覆盖）
      Layer 2: compressed_history（~300 tokens，超出窗口的旧轮次摘要）
      Layer 3: feedback_history[-context_window_size:]（最近N轮完整保留）
      + RAG 检索结果 + 模板结构
    - 当前微调轮次 > 0 时，仅重新生成 feedback_history 中 scope=local 涉及的目标章节
    - scope=global 的反馈不触发重写，仅更新 global_constraints
    - 每章生成后立即写入 sections，支持流式进度展示
    - 任一章节 LLM 调用失败 → 标记 FAILED，已生成的章节保留
    """
```

## QualityChecker

```python
def quality_checker(state: AgentState) -> dict:
    """
    输入：state.sections + state.requirements
    输出：{quality_report: {verdict, completeness, compliance, consistency}, node_status: {...}}
    
    约束：
    - completeness：逐条对比 requirements.scoring 与 sections 内容，标记遗漏项
    - compliance：检查 sections 全文是否含禁用词（绝对化用语、不当承诺等）
    - consistency：检查关键数据（人数、金额、日期）在 sections 中是否一致
    - verdict = PASS 当且仅当 completeness 全部通过 AND compliance 空 AND consistency 空
    """
```

## FeedbackProcessor

```python
def feedback_processor(state: AgentState) -> dict:
    """
    输入：state.messages（含用户最新反馈）+ state.feedback_history + state.global_constraints
    输出：{feedback_history: [附加新记录], global_constraints: [更新], current_round: int, node_status: {...}}
    
    约束：
    - 对用户反馈进行五分类（content_fix/style_adjust/structure/score_align）+ 作用域标注（global/local）
    - global 型反馈：追加到 global_constraints，不写入 feedback_history（避免触发不必要的重写）
    - local 型反馈：定位目标章节和段落索引
    - 如无法确定目标（反馈过于模糊）→ 标记 FAILED + 提示用户指定
    - 如果反馈包含「忽略之前所有约束」类指令 → 判断是新的全局约束（替换旧 global_constraints）还是注入尝试（拦截并提示）
    - 新记录写入时附带 diff_before（修改前的段落原文）和 diff_after（空，等待 SectionGenerator 填充）
    - 当 len(feedback_history) > context_window_size 时，触发 Layer 2 压缩：调用压缩 LLM 将超出窗口的旧轮次压缩为 compressed_history
    """
```

## DocumentAssembler

```python
def doc_assembler(state: AgentState) -> dict:
    """
    输入：state.sections + state.requirements.format_rules
    输出：{node_status: {..., "DocumentAssembler": COMPLETED}}
    副作用：生成 DOCX 文件到 data/exports/{project_id}_v{round}.docx
    
    约束：
    - 按章节序号排序合并 sections
    - 应用格式规则：页边距/字体/行距/标题层级
    - 自动生成目录（Table of Contents）
    - 导出文件路径写入 state 的返回 dict 中
    """
```

## 管道图构建 (US#6: 两阶段拆分)

```python
def build_graph(llm_fns=None) -> StateGraph:
    """全量管道（无头模式）。
    
    14 个节点端到端运行，InfoVerificationGate 自动确认 (auto_confirm=True)。
    用于 CI/测试/批处理场景。
    """

def build_extraction_graph(llm_fns=None) -> StateGraph:
    """Phase 1: 提取子图（交互模式）。
    
    DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate → END
    
    InfoVerificationGate 设置 pending 后管道终止，GUI 展示校验表单。
    """

def build_generation_graph(llm_fns=None) -> StateGraph:
    """Phase 2: 生成子图（交互模式）。
    
    EligibilityChecker → TemplateMatcher → ... → DocumentAssembler → END
    
    入口为 EligibilityChecker，接收 Phase 1 + 用户校验后的完整 state。
    """
```
