"""SectionGenerator node — generates bid section content via LLM.

Contract (from node_interfaces.md):
    def section_generator(state: AgentState) -> dict
    Input:  state.requirements, state.selected_template_id, state.sections,
            state.bid_type, state.feedback_history,
            state.global_constraints, state.compressed_history,
            state.context_window_size  (R1: 三层反馈历史消费，specs/010 批次1)
    Output: {sections: {section_name: content}, node_status: {...}}

    R1 note: 三层反馈消费发生在 generate_all_sections_parallel（graph.py:56
    生产接线路径）；本文件内的 section_generator() 单段路径不消费反馈。

Supports:
    - LLM injection via functools.partial (Phase A1)
    - Targeted revision via feedback_history (Phase A2)
    - Template-driven section selection (Phase A3)
    - Bid-type-aware prompt selection (Phase B2/B3)
    - Parallel subgraph generation (T015) with error isolation (Phase B5)
"""

import logging
from typing import Callable

from core.nodes.feedback_processor import (
    DEFAULT_CONTEXT_WINDOW_SIZE,
    get_recent_context_window,
)
from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Anti-fabrication directive ─────────────────────────────────────────
# Injected into ALL section prompts. Prevents the LLM from fabricating
# real-world data (personnel names, company info, certificate numbers,
# project performance, etc.) that the user hasn't provided.
# Instead, the LLM must use 【待填写：具体说明】 placeholders, which are
# automatically rendered in red bold in the DOCX output to flag them
# for manual editing.

NO_FABRICATION_DIRECTIVE = """

【真实数据防虚构指令 — 最高优先级，覆盖一切其他指令】
你正在为真实的招投标项目生成标书内容。以下类别属于"真实数据"，严禁凭空编造：

1. 人员信息：姓名、性别、年龄、职务、身份证号、学历、证书编号
2. 企业信息：公司名称、注册地址、注册资金、成立时间、员工总数、税号、银行账号
3. 证书信息：证书名称、证书编号、发证机构、有效期
4. 财务数据：营业额、利润、资产总额、审计数据
5. 项目业绩：项目名称、合同金额、甲方名称、合同日期、服务人数
6. 投标数据：报价金额、税率、保证金金额
7. 法律数据：诉讼/仲裁案件信息
8. 联系方式：电话、传真、邮编、网址

处理规则：
- 如果项目上下文契约中已提供该数据 → 直接使用契约中的值，不得修改
- 如果招标文件中包含该数据 → 直接引用，不得修改
- 如果以上来源均未提供该数据 → 必须使用占位符格式：【待填写：具体说明】
  · 正确示例：【待填写：法定代表人姓名】
  · 正确示例：【待填写：劳务派遣经营许可证编号】
  · 正确示例：【待填写：近三年营业额，需与审计报告一致】
  · 正确示例：【待填写：团队成员姓名、从业年限、专业证书编号】
  · 错误示例：张振华（编造的人名）
  · 错误示例：证书编号RLP20240001（编造的编号）
  · 错误示例：¥1,280,000（编造的金额）

为什么这样设计：占位符在导出的 Word 文档中会自动标红加粗显示，提醒投标人手动填写真实信息。编造数据会导致废标或法律风险。

【提交形式一致性指令 — 同一章节内不得自相矛盾】
标书中许多事项存在多种互斥的提交/处理方式，在同一章节中只能选择一种，严禁混用。典型互斥场景：
1. 投标保证金：银行保函 vs 银行转账/电汇 — 保函需担保人签章，转账需附回单，二者不可同时出现
2. 投标报价：总价包干 vs 单价计价 — 同一项目不得混合使用两种计价方式
3. 履约保证：履约保函 vs 履约保证金 — 不得在同一章节中同时要求两种担保形式
4. 质保方式：质保金 vs 质保函 — 不得混合使用
5. 合同类型：固定总价合同 vs 固定单价合同 vs 成本加酬金合同 — 不得混用

处理规则：
- 如招标文件已明确指定方式 → 严格按招标文件要求输出，不得出现另一种方式的内容
- 如招标文件未明确指定 → 输出选择说明【待填写：请根据招标文件要求选择XXX方式】，然后分别列出各方式的模板，标注"如选择此方式，保留并填写，删除其他方式"
- 禁止：在同一章节正文中同时出现两种互斥方式的格式要素（如保函担保人签章 + 转账回单附件）
"""

# ── Standard bid document sections (generic fallback) ──────────────────

DEFAULT_SECTIONS = [
    {"name": "第一章 投标函", "key": "ch1_letter"},
    {"name": "第二章 法定代表人授权委托书", "key": "ch2_authorization"},
    {"name": "第三章 服务方案", "key": "ch3_service"},
    {"name": "第四章 技术方案", "key": "ch4_technical"},
    {"name": "第五章 人员配置", "key": "ch5_staffing"},
    {"name": "第六章 公司资质与业绩", "key": "ch6_qualifications"},
    {"name": "第七章 项目实施计划", "key": "ch7_schedule"},
    {"name": "第八章 售后服务承诺", "key": "ch8_after_sales"},
]


# ── Section-specific prompts ───────────────────────────────────────────

# Shared instruction for chapters that benefit from visual diagrams.
# Injected into prompts for personnel structure, workflow, and process chapters.
# Section keys that should have diagram instructions injected.
# These are chapters that typically contain personnel structure,
# workflow, or process descriptions.
_DIAGRAM_SECTIONS = {"ch3_service", "ch5_staffing", "ch7_schedule"}

_DIAGRAM_INSTRUCTION = """

【图表插入要求】
在涉及人员架构、工作流程、组织层级等内容时，必须使用 Mermaid 语法绘制图表，使内容更直观专业。

使用规则：
1. 人员架构/组织结构 → 使用 flowchart 图表（自上而下层级图）
2. 工作流程/审批流程 → 使用 flowchart 图表（从左到右流程图）
3. 项目实施阶段 → 使用 flowchart 图表（时间线/阶段图）
4. 应急响应流程 → 使用 flowchart 图表（决策分支图）

格式要求：
- 将 Mermaid 代码放在 ```mermaid 代码块中
- 每个图表前用一句话引出（如"下图展示了项目团队组织架构："）
- 节点文字使用中文，简明扼要
- 人员架构图使用 TD（自上而下）方向
- 流程图使用 LR（从左到右）方向
- 图表节点不超过 15 个，保持简洁

示例（人员架构）：
下图展示了项目团队组织架构：
```mermaid
flowchart TD
    A[项目负责人] --> B[入职资料及劳动合同组]
    A --> C[薪酬数据及社保组]
    A --> D[培训管理组]
    A --> E[劳动争议及工伤管理组]
    A --> F[驻厂管理组]
```

示例（工作流程）：
下图展示了人员入职流程：
```mermaid
flowchart LR
    A[需求确认] --> B[人员招聘]
    B --> C[背景核查]
    C --> D[签订合同]
    D --> E[岗前培训]
    E --> F[上岗作业]
```
"""

_SECTION_PROMPTS = {
    "ch1_letter": """你是一位资深招投标专家。请撰写"第一章 投标函"。

要求：
- 格式规范：包含投标人名称【待填写：投标人全称】、项目名称【待填写：项目名称，须与招标文件一致】、招标编号【待填写：招标编号】
- 明确投标有效期（一般为90天）
- 承诺按照招标文件要求提供服务和产品
- 声明已仔细阅读全部招标文件内容
- 附上投标保证金金额（如果有）【待填写：投标保证金金额】
- 投标报价【待填写：报价金额大写及小写】

以下是招标文件的评分要求：
{requirements_context}

请直接输出章节内容，不要输出标题。""",

    "ch2_authorization": """你是一位资深招投标专家。请撰写"第二章 法定代表人授权委托书"。

要求：
- 包含法定代表人姓名【待填写：法定代表人姓名】、职务【待填写：法定代表人职务】
- 被授权人姓名【待填写：被授权人姓名】、职务【待填写：被授权人职务】
- 授权范围：代表公司参与全程投标活动
- 授权期限【待填写：授权起止日期】
- 附法定代表人身份证号【待填写：法定代表人身份证号】和被授权人身份证号【待填写：被授权人身份证号】

招标文件要求：
{requirements_context}

请直接输出章节内容。""",

    "ch3_service": """你是一位资深招投标专家。请撰写"第三章 服务方案"。

要求（根据评分标准逐条回应）：
- 每个评分项必须单独成段论述
- 用具体数据支撑（人数、时间、流程）
- 突出差异化优势（如：本地化服务网络、信息化平台）
- 语言专业严谨，避免空话套话
- 涉及服务流程、工作流程时，必须用 Mermaid 流程图展示

评分要求：
{requirements_context}

请按照评分项逐个展开，直接输出章节内容。""",

    "ch4_technical": """你是一位资深招投标专家。请撰写"第四章 技术方案"。

要求：
- 针对招标文件中的技术规格一一回应
- 技术参数需要具体、可验证
- 如有强制性要求，必须明确标注"完全满足"
- 方案应体现先进性、可行性、创新性

技术规格：
{requirements_context}

请直接输出章节内容。""",

    "ch5_staffing": """你是一位资深招投标专家。请撰写"第五章 人员配置方案"。

要求：
- 列出项目团队组织结构图（必须使用 Mermaid flowchart 图表展示）
- 关键岗位人员资质和证书：所有人员姓名、证书编号等真实信息一律用【待填写：具体说明】占位，严禁编造
- 人员数量和专业分布：可按岗位类型给出配置建议，但具体人员信息用占位符
- 人员调配和培训计划
- 涉及人员管理流程时使用 Mermaid 流程图展示
- 严禁编造任何人员姓名、身份证号、学历、证书编号等真实数据

项目要求：
{requirements_context}

请直接输出章节内容。""",

    "ch6_qualifications": """你是一位资深招投标专家。请撰写"第六章 公司资质与业绩"。

要求：
- 逐条列出相关资质证书（ISO、行业许可证等）：证书名称可列出，证书编号、有效期一律用【待填写：具体说明】占位
- 列出近3年同类项目业绩：所有项目名称、金额、周期、甲方单位等真实信息一律用【待填写：具体说明】占位，严禁编造业绩项目
- 每个业绩需包含：项目名称【待填写】、金额【待填写】、周期【待填写】、甲方单位【待填写】
- 严禁编造任何企业信息、证书编号、项目业绩数据

资质要求：
{requirements_context}

请直接输出章节内容。""",

    "ch7_schedule": """你是一位资深招投标专家。请撰写"第七章 项目实施计划"。

要求：
- 分阶段描述（需求调研→方案设计→实施→测试→验收→运维）
- 每个阶段有时间节点和交付物
- 标注关键里程碑
- 资源配置计划
- 必须使用 Mermaid flowchart 图表展示项目实施阶段和里程碑

招标文件要求：
{requirements_context}

请直接输出章节内容。""",

    "ch8_after_sales": """你是一位资深招投标专家。请撰写"第八章 售后服务承诺"。

要求：
- 质保期（至少1年，越长越好）
- 响应时间（7x24小时，XX小时内到场）
- 服务网点分布
- 定期巡检计划
- 培训和知识转移

招标文件要求：
{requirements_context}

请直接输出章节内容。""",
}


# ── Helpers ────────────────────────────────────────────────────────────


def _build_requirements_context(state: AgentState) -> str:
    """Build a text summary of requirements for injection into prompts."""
    reqs = state.get("requirements", {})
    parts: list[str] = []

    scoring = reqs.get("scoring", [])
    if scoring:
        parts.append("评分标准：")
        for item in scoring:
            parts.append(f"  - {item.get('item_name', '')}（{item.get('score', 0)}分）：{item.get('criteria', '')}")

    tech = reqs.get("tech_specs", [])
    if tech:
        parts.append("\n技术规格：")
        for spec in tech:
            mandatory_label = "【强制】" if spec.get("mandatory") else ""
            parts.append(f"  - {mandatory_label} {spec.get('spec_name', '')}: {spec.get('requirement', '')}")

    quals = reqs.get("qualifications", [])
    if quals:
        parts.append("\n资质要求：")
        for q in quals:
            parts.append(f"  - {q}")

    return "\n".join(parts) if parts else "无特定评分/技术要求"


def _get_sections_for_bid_type(bid_type: str) -> list[dict]:
    """Return the section set appropriate for the bid type.

    Phase B2/B3: 劳务管理服务类/劳务外包类 uses the optimized 8-part structure
    derived from a real winning bid (投标函/身份证明/授权委托书/保证金/偏差表/
    资格审查/服务方案/应急保障).  Other types fall back to the generic DEFAULT_SECTIONS.
    """
    if bid_type and ("劳务管理服务" in bid_type or "劳务外包" in bid_type):
        from core.nodes.optimized_prompts import OPTIMIZED_SECTIONS
        return OPTIMIZED_SECTIONS
    return DEFAULT_SECTIONS


def _get_contract_summary(state: AgentState) -> str:
    """Extract contract summary from state.project_contract if available.

    Returns empty string when no contract is present (backward compatible).
    """
    contract_data = state.get("project_contract", {})
    if not contract_data:
        return ""
    try:
        from core.contract import ProjectContextContract
        contract = ProjectContextContract.from_dict(contract_data)
        return contract.summary()
    except Exception as e:
        logger.debug(f"Contract summary extraction failed: {e}")
        return ""


def _build_contract_block(contract_summary: str) -> str:
    """Build the contract injection block for section prompts."""
    if not contract_summary:
        return ""
    return (
        "【项目上下文契约 — 必须严格遵守，偏离将整章打回重生】\n"
        f"{contract_summary}\n\n"
        "⚠️ 严禁偏离上述项目名称/投标人/项目地点/行业属性。\n\n"
    )


def _get_section_name_from_key(section_key: str) -> str:
    """根据 section_key 查找对应的章节名称，用于经验匹配。"""
    for s in DEFAULT_SECTIONS:
        if s["key"] == section_key:
            return s["name"]
    try:
        from core.nodes.optimized_prompts import OPTIMIZED_SECTIONS
        for s in OPTIMIZED_SECTIONS:
            if s["key"] == section_key:
                return s["name"]
    except ImportError:
        pass
    return section_key


def _get_consistency_lesson_directive(section_key: str, bid_type: str = "") -> str:
    """获取并构建一致性经验指令块，注入到 prompt 中。

    从 ConsistencyLessonStore 中查询与当前章节相关的历史经验教训，
    拼接成指令文本返回。失败时静默返回空字符串（不阻断生成流程）。
    """
    try:
        from core.evolution.consistency_lesson_store import ConsistencyLessonStore
        store = ConsistencyLessonStore()
        section_name = _get_section_name_from_key(section_key)
        return store.build_directive_block(section_name, bid_type)
    except Exception as e:
        logger.debug(f"Consistency lesson injection skipped: {e}")
        return ""


def _inject_table_template(section_key: str, bid_type: str, bid_subtype: str) -> str:
    """95+优化 补强五: 加载子类型专用的表格模板, 注入到资格审查章提示词中.

    当 section_key 为 sec6_qualification / ch6_qualifications 且 bid_subtype 存在时,
    从 knowledge_base/劳务外包类/{bid_subtype}/patterns/ 加载结构化 JSON 模板,
    转为 Markdown 表格格式注入提示词.

    数据格式与 ingest_real_bid.extract_and_save_tables 写入格式一致:
        header: list[str], rows: list[list[str]], type: str
    """
    if section_key not in ("sec6_qualification", "ch6_qualifications"):
        return ""
    if not bid_type or not bid_subtype:
        return ""

    try:
        import json
        from pathlib import Path
        kb_root = Path(__file__).parent.parent.parent.parent / "knowledge_base"
        # 归一化 bid_type → KB 目录名: 劳务管理服务类/劳务外包类 都映射到 劳务外包类
        kb_type = "劳务外包类" if ("劳务" in bid_type) else bid_type
        table_dir = kb_root / kb_type / bid_subtype / "patterns"
        if not table_dir.exists():
            return ""

        templates = []
        for json_file in table_dir.glob("*.json"):
            data = json.loads(json_file.read_text(encoding="utf-8"))
            # 数据格式: header (list[str]), rows (list[list[str]])
            header = data.get("header", [])
            rows = data.get("rows", [])
            if not header or not rows:
                continue
            # 仅加载资格审查类型表格
            if data.get("type", "") not in ("qualification", "unknown"):
                continue

            # 构建 Markdown 表格
            header_cells = [str(c) for c in header]
            md_lines = [
                f"**{data.get('type', '资格审查表')}表 (来源: {data.get('source', '')})**",
                "",
                "| " + " | ".join(header_cells) + " |",
                "|" + "|".join(["---"] * len(header_cells)) + "|",
            ]
            for row in rows:
                cells = [str(row[i]) if i < len(row) else "" for i in range(len(header_cells))]
                md_lines.append("| " + " | ".join(cells) + " |")

            templates.append("\n".join(md_lines))

        if templates:
            return (
                "\n\n【资格审查表结构化模板 — 请按此结构填写】\n"
                + "\n\n".join(templates)
            )
    except Exception as e:
        logger.debug(f"Table template injection skipped: {e}")
    return ""


def _get_section_prompt(section_key: str, requirements_context: str, bid_type: str = "",
                        contract_summary: str = "", bid_subtype: str = "") -> str:
    """Get the prompt template for a section, with requirements injected.

    Phase B2: when ``bid_type`` is 劳务管理服务类, look up the optimized prompt
    first (the optimized keys like ``sec1_bid_letter`` override the generic
    ``ch1_letter`` keys).  Falls back to the generic prompt set otherwise.

    P1-2: injects a reference essay snippet (RAG) retrieved from the
    knowledge base, grounding the LLM on real winning bids.

    Phase 007: injects accumulated consistency lessons from
    ConsistencyLessonStore, so the LLM avoids repeating past mistakes.
    """
    # Try optimized prompts for 劳务管理服务类 / 劳务外包类
    if bid_type and ("劳务管理服务" in bid_type or "劳务外包" in bid_type):
        try:
            from core.nodes.optimized_prompts import get_optimized_section_prompt
            if section_key.startswith("sec"):
                base_prompt = get_optimized_section_prompt(section_key, requirements_context)
                # P1-2: RAG injection
                ref_snippet = _retrieve_reference(section_key, bid_type, bid_subtype=bid_subtype)
                if ref_snippet:
                    base_prompt += f"\n\n{ref_snippet}"
                # 95+优化 补强五: 表格模板注入
                table_tmpl = _inject_table_template(section_key, bid_type, bid_subtype)
                if table_tmpl:
                    base_prompt += table_tmpl
                # Inject anti-fabrication directive
                base_prompt += NO_FABRICATION_DIRECTIVE
                # Phase 007: inject consistency lessons
                lesson_directive = _get_consistency_lesson_directive(section_key, bid_type)
                if lesson_directive:
                    base_prompt += lesson_directive
                # Contract injection
                contract_block = _build_contract_block(contract_summary)
                if contract_block:
                    base_prompt = contract_block + base_prompt
                return base_prompt
        except Exception as e:
            logger.warning(f"Optimized prompt lookup failed for '{section_key}': {e}")

    template = _SECTION_PROMPTS.get(section_key, "")
    if not template:
        prompt = f"请按要求撰写章节内容。\n\n{requirements_context}"
    else:
        prompt = template.format(requirements_context=requirements_context)

    # Inject anti-fabrication directive into ALL generic prompts
    prompt += NO_FABRICATION_DIRECTIVE

    # Inject diagram instruction for chapters that benefit from visual
    # diagrams (personnel structure, workflow, process, schedule)
    if section_key in _DIAGRAM_SECTIONS:
        prompt += _DIAGRAM_INSTRUCTION

    # P1-2: RAG injection for generic prompts too
    ref_snippet = _retrieve_reference(section_key, bid_type, bid_subtype=bid_subtype)
    if ref_snippet:
        prompt += f"\n\n{ref_snippet}"

    # 95+优化 补强五: 表格模板注入 (generic prompts)
    table_tmpl = _inject_table_template(section_key, bid_type, bid_subtype)
    if table_tmpl:
        prompt += table_tmpl

    # Phase 007: inject consistency lessons for generic prompts too
    lesson_directive = _get_consistency_lesson_directive(section_key, bid_type)
    if lesson_directive:
        prompt += lesson_directive

    # Contract injection
    contract_block = _build_contract_block(contract_summary)
    if contract_block:
        prompt = contract_block + prompt
    return prompt


def _retrieve_reference(section_key: str, bid_type: str, bid_subtype: str = "") -> str:
    """Retrieve a reference essay snippet for RAG injection.

    95+优化 补强二: 当 bid_subtype 存在时, 优先搜索子类型分区的 chunks 目录.
    Returns an empty string if no references are found (non-fatal).
    """
    try:
        from core.retrieval.reference_retriever import get_reference_retriever
        retriever = get_reference_retriever()
        # Build a query hint from the section key (convert sec7_service_plan → "服务方案")
        query_hint = section_key.replace("_", " ").replace("sec", "").replace("ch", "").strip()
        snippet = retriever.retrieve_for_section(section_key, bid_type, query_hint, bid_subtype=bid_subtype)
        if snippet:
            logger.debug(f"RAG: injected {len(snippet)} chars of reference for '{section_key}'")
        return snippet
    except Exception as e:
        logger.debug(f"RAG retrieval skipped for '{section_key}': {e}")
        return ""


def _collect_section_feedback(state: AgentState) -> dict[str, list[str]]:
    """Collect feedback items keyed by target section.

    Phase A2 fix: previously feedback_history was never read by the generator,
    so the FAIL→Feedback→Generator loop just re-emitted the old content.
    Now we extract per-section revision instructions so the generator can
    perform *targeted* regeneration instead of skipping already-built sections.

    R02 fix: entries with ``target_section=""`` (e.g. from human review
    rejection via ``resume_after_review``) are treated as **global feedback**
    and applied to ALL existing sections.

    R1 fix (specs/010 批次1, Layer 3): 修订指令仅取最近 ``context_window_size``
    轮反馈（get_recent_context_window）。更早轮次的反馈不再进入定向修订指令，
    而是以 Layer 1（全局约束）/ Layer 2（压缩摘要）形式保留在 prompt 中，
    避免 prompt 随轮次无限膨胀。

    注意（有意权衡）：scope=global 的窗口内反馈会同时出现在 Layer 1 约束块与
    本函数产出的修订指令中（token 轻度冗余）。不跳过它们的原因：global 反馈
    必须触发「已生成章节强制再生成」（R02 fix），仅靠 Layer 1 注入无法让
    无 local 反馈的章节进入再生成集合。

    Returns:
        {section_name: [feedback_text, ...]} — only sections with feedback.
    """
    feedback_history = state.get("feedback_history", []) or []
    # 与写侧（feedback_processor）一致做 None/0 归一：异常值回退默认窗口 3
    window_size = state.get("context_window_size") or DEFAULT_CONTEXT_WINDOW_SIZE
    recent_history = get_recent_context_window(feedback_history, window_size)

    by_section: dict[str, list[str]] = {}
    global_feedbacks: list[str] = []
    for record in recent_history:
        target = record.get("target_section", "")
        text = record.get("feedback_text", "")
        if not text:
            continue
        if target:
            by_section.setdefault(target, []).append(text)
        else:
            # Global feedback (e.g. human review rejection) — applies to all
            global_feedbacks.append(text)

    # Apply global feedbacks to all existing sections
    if global_feedbacks:
        existing_sections = state.get("sections", {})
        for section_name in existing_sections:
            by_section.setdefault(section_name, []).extend(global_feedbacks)

    return by_section


# ── Three-layer feedback blocks (R1, specs/010 批次1) ──────────────────
# 修复断裂点 A：FeedbackProcessor 写侧（M4）正确回写三层结构到 state，
# 但生成侧此前零消费。以下两个块构造函数 + Layer 3 窗口（见上）补全消费链。

GLOBAL_CONSTRAINTS_HEADER = "【全局硬性约束（历轮累积，任何章节必须遵守）】"
COMPRESSED_HISTORY_HEADER = "【历史反馈摘要（去重压缩，供参考，勿重蹈覆辙）】"
COMPRESSED_HISTORY_MAX_CHARS = 500


def _build_global_constraints_block(constraints: list[str]) -> str:
    """Layer 1 — 全局硬性约束块（置顶注入所有生成中的章节 prompt）。

    来自 state.global_constraints（FeedbackProcessor 跨轮累积、去重、
    永不丢弃的 scope=global 反馈）。保证多轮微调时全局硬性约束不丢失
    （PRD §4.1.5「定向修订不丢历史约束」）。空约束返回空串（首轮零注入）。
    注意：约束不得与系统级防虚构指令冲突，冲突时以系统指令为准。
    """
    if not constraints:
        return ""
    lines = [GLOBAL_CONSTRAINTS_HEADER + "（不得与系统级防虚构指令冲突，冲突时以系统指令为准）"]
    lines.extend(f"  {i}. {c}" for i, c in enumerate(constraints, 1))
    return "\n".join(lines) + "\n\n"


def _build_compressed_history_block(
    compressed: str, max_chars: int = COMPRESSED_HISTORY_MAX_CHARS
) -> str:
    """Layer 2 — 压缩历史摘要块（限长截断，附于章节提示之后）。

    来自 state.compressed_history（同类反馈去重合并后的历史摘要字符串）。
    窗口外旧轮次反馈靠本块保留线索；限长避免 prompt 膨胀。空串返回空串。
    """
    if not compressed:
        return ""
    truncated = compressed[:max_chars]
    suffix = "…（超长已截断）" if len(compressed) > max_chars else ""
    return f"\n\n{COMPRESSED_HISTORY_HEADER}\n{truncated}{suffix}"


def _build_revision_context(feedback_texts: list[str]) -> str:
    """Format feedback items into a revision instruction block for the prompt.

    R1 (Layer 3): feedback_texts 已经过近 N 轮窗口过滤（见
    _collect_section_feedback），文案相应表述为「近几轮」。
    """
    if not feedback_texts:
        return ""
    lines = ["\n\n【近几轮质检反馈，请针对性修订】"]
    for i, fb in enumerate(feedback_texts, 1):
        lines.append(f"  {i}. {fb}")
    lines.append("请在本次生成中直接解决以上问题，不要回避。")
    return "\n".join(lines)


def _resolve_sections_from_template(state: AgentState) -> list[dict]:
    """Resolve which sections to generate based on the selected template.

    Phase A3 fix: previously SectionGenerator ignored ``selected_template_id``
    entirely and always used the full ``DEFAULT_SECTIONS`` list.  Now we look
    up the matched template in the template library and, if found, generate
    only the sections the template prescribes.  Falls back to all default
    sections when no template is selected (preserving backward compatibility).

    Phase B3: when ``bid_type`` is set (e.g. 劳务管理服务类) and no template is
    matched, use the bid-type-specific section set instead of the generic one.
    """
    bid_type = state.get("bid_type", "")
    selected_id = state.get("selected_template_id", "")

    # Phase B3 fix: 劳务外包类/劳务管理服务类 always uses the optimized
    # 9-section structure from real winning bids, regardless of template
    # matching.  Template matching with MockEmbedder can select wrong-type
    # templates (e.g. tpl_service_v1 from 服务类 with only 4 sections),
    # which would incorrectly restrict the section set.
    if bid_type and ("劳务管理服务" in bid_type or "劳务外包" in bid_type):
        return _get_sections_for_bid_type(bid_type)

    if not selected_id:
        # No template → use bid-type-aware section set (Phase B3)
        return _get_sections_for_bid_type(bid_type)

    # Look up the template across all bid types
    try:
        from core.retrieval.template_library import TEMPLATES
        for templates in TEMPLATES.values():
            for tpl in templates:
                if tpl.get("id") == selected_id:
                    section_keys = set(tpl.get("sections", []))
                    resolved = [s for s in DEFAULT_SECTIONS if s["key"] in section_keys]
                    if resolved:
                        logger.info(
                            f"Using template '{selected_id}' → {len(resolved)} sections: "
                            f"{[s['key'] for s in resolved]}"
                        )
                        return resolved
                    break  # template found but no matching section keys
    except Exception as e:
        logger.warning(f"Template lookup failed for '{selected_id}': {e}")

    return DEFAULT_SECTIONS


# ── Mock LLM for testing ──────────────────────────────────────────────


def _mock_llm_fn(section_name: str) -> Callable:
    """Simple mock that returns formatted content for testing."""
    def fn(prompt: str) -> str:
        return (
            f"[{section_name}]\n"
            f"这是{section_name}的生成内容。"
            f"包含专业论述和具体数据。"
            f"论述详细完整，数据翔实可查。"
            f"完全满足招标文件的所有要求。"
            f"方案经过充分论证和反复优化。"
        )
    return fn


def _order_sections(sections: dict[str, str], section_order: list[dict]) -> dict[str, str]:
    """Reorder sections dict to match the defined section order.

    Parallel generation via ThreadPoolExecutor returns results in completion
    order (random), not the defined chapter order. This function reorders the
    merged dict so that downstream display/export follows the correct sequence.

    Sections not in the order list (e.g. legacy entries) are appended at the end.
    """
    ordered: dict[str, str] = {}
    for s in section_order:
        name = s["name"]
        if name in sections:
            ordered[name] = sections[name]
    # Append any remaining sections not in the defined order
    for name, content in sections.items():
        if name not in ordered:
            ordered[name] = content
    return ordered


# ── LangGraph Node ─────────────────────────────────────────────────────


def section_generator(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
    sections_to_generate: list[dict] | None = None,
) -> dict:
    """Generate bid section content via LLM.

    Args:
        state: AgentState with requirements, template, and optional partial sections
        llm_fn: LLM call function (prompt) -> text. Uses mock if None.
        sections_to_generate: Optional subset of DEFAULT_SECTIONS to generate.
                              If None, generates all sections not yet in state.sections.

    Returns:
        Dict with updated sections and node_status
    """
    requirements_context = _build_requirements_context(state)
    contract_summary = _get_contract_summary(state)
    # Phase A3/B3: respect template-selected section set when no explicit override
    target_sections = sections_to_generate or _resolve_sections_from_template(state)
    existing_sections = state.get("sections", {})
    bid_type = state.get("bid_type", "")
    bid_subtype = state.get("bid_subtype", "")  # 95+优化 补强二

    new_sections: dict[str, str] = {}

    # In production, SectionGenerator only generates ONE section per call
    # (iterates via the graph loop). For initial MVP, generate all.
    for section in target_sections:
        section_key = section["key"]
        section_name = section["name"]

        # Skip if already generated (assuming already in state, not regenerating)
        if section_name in existing_sections and existing_sections[section_name]:
            logger.debug(f"Skipping {section_name} — already generated")
            continue

        prompt = _get_section_prompt(section_key, requirements_context, bid_type=bid_type,
                                     contract_summary=contract_summary, bid_subtype=bid_subtype)

        if llm_fn is None:
            # Check global LLM config (set by GUI)
            from core.graph import get_pipeline_llm
            global_llm = get_pipeline_llm()
            if global_llm is not None:
                content = global_llm(prompt)
            else:
                # Mock for testing
                content = _mock_llm_fn(section_name)(prompt)
        else:
            content = llm_fn(prompt)

        new_sections[section_name] = content
        logger.info(f"Generated {section_name}: {len(content)} chars")

    # Merge with existing, then reorder by defined section order
    merged_sections = {**existing_sections, **new_sections}
    merged_sections = _order_sections(merged_sections, target_sections)
    current_section = next(iter(new_sections.keys()), "") if new_sections else state.get("current_section", "")

    return {
        "sections": merged_sections,
        "current_section": current_section,
        "node_status": {
            **state.get("node_status", {}),
            "SectionGenerator": NodeStatus.COMPLETED.value,
        },
    }


# ── Parallel Subgraph (T015) ──────────────────────────────────────────

# LangGraph parallelism uses Send API. For now, we provide a helper that
# runs section generation concurrently via threading.

def generate_all_sections_parallel(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Generate all 8 sections in parallel using threading.

    For use in LangGraph Send-based parallel subgraphs (T015).

    Phase A2 fix: reads ``feedback_history`` to decide which already-generated
    sections must be *regenerated* (targeted revision) rather than skipped.
    Previously the ``if name in existing: return existing[name]`` guard meant
    the FAIL→Feedback→Generator retry loop was a no-op — the same content was
    returned every round and the quality issue was never actually fixed.

    Args:
        state: AgentState
        llm_fn: LLM function

    Returns:
        Dict with all sections merged
    """
    import concurrent.futures
    import threading

    requirements_context = _build_requirements_context(state)
    contract_summary = _get_contract_summary(state)
    existing = state.get("sections", {})
    # Per-section feedback → forces regeneration of that section
    section_feedback = _collect_section_feedback(state)
    # R1（specs/010 批次1）Layer 1/2：消费 FeedbackProcessor 写侧回写的三层
    # 结构中与单章节无关的两层——全局硬性约束（置顶）与压缩历史摘要（限长）。
    # 干净 state 下两者为空串，prompt 与修复前逐字节一致（零行为变更守卫）。
    l1_block = _build_global_constraints_block(state.get("global_constraints", []) or [])
    l2_block = _build_compressed_history_block(state.get("compressed_history", "") or "")
    # Phase A3/B3: resolve section set from selected template or bid_type
    target_sections = _resolve_sections_from_template(state)
    bid_type = state.get("bid_type", "")
    bid_subtype = state.get("bid_subtype", "")  # 95+优化 补强二

    lock = threading.Lock()
    results: dict[str, str] = {}

    def generate_one(section: dict) -> tuple[str, str]:
        key = section["key"]
        name = section["name"]
        feedback_texts = section_feedback.get(name, [])

        # Skip only when the section already has content AND has no feedback.
        # If there is feedback for this section, we must regenerate it so the
        # revision actually happens (this is the core of the A2 fix).
        if name in existing and existing[name] and not feedback_texts:
            return name, existing[name]

        prompt = _get_section_prompt(key, requirements_context, bid_type=bid_type,
                                     contract_summary=contract_summary, bid_subtype=bid_subtype)
        # R1 注入顺序：Layer 1 全局约束置顶（不可变硬约束优先级最高）
        # → 章节提示主体 → Layer 2 压缩历史 → Layer 3 定向修订指令。
        prompt = l1_block + prompt + l2_block
        # Inject revision context so the LLM addresses the specific issues
        prompt = prompt + _build_revision_context(feedback_texts)

        if llm_fn:
            content = llm_fn(prompt)
        else:
            # Fall back to global LLM (set by GUI) before mock
            from core.graph import get_pipeline_llm
            global_llm = get_pipeline_llm()
            if global_llm is not None:
                content = global_llm(prompt)
            else:
                content = _mock_llm_fn(name)(prompt)
        return name, content

    # Sections that need generation: either not yet built, or flagged for revision
    pending = [
        s for s in target_sections
        if s["name"] not in existing
        or not existing[s["name"]]
        or s["name"] in section_feedback
    ]

    if not pending:
        logger.info("All sections already generated — skipping parallel generation")
        return {
            "sections": existing,
            "node_status": {
                **state.get("node_status", {}),
                "SectionGenerator": NodeStatus.COMPLETED.value,
            },
        }

    workers = max(1, min(8, len(pending)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_section = {executor.submit(generate_one, s): s for s in pending}
        for future in concurrent.futures.as_completed(future_to_section):
            try:
                name, content = future.result()
            except Exception as e:
                # Phase B5 (error isolation) — placeholder content on failure
                failed_section = future_to_section[future]["name"]
                logger.error(f"Section generation failed for {failed_section}: {e}")
                name, content = failed_section, f"【生成失败：{failed_section} — 请手动补充】"
            with lock:
                results[name] = content

    merged = {**existing, **results}
    # Reorder sections by defined order (parallel completion order is random)
    merged = _order_sections(merged, target_sections)
    revised = [n for n in results if n in section_feedback]
    logger.info(
        f"Parallel generation: {len(results)} sections "
        f"({len(revised)} targeted revisions from feedback)"
    )

    return {
        "sections": merged,
        "node_status": {
            **state.get("node_status", {}),
            "SectionGenerator": NodeStatus.COMPLETED.value,
        },
    }
