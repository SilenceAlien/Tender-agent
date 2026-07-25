"""ConsistencyLessonExtractor — 从一致性问题提取可复用经验。

当 CrossReferenceChecker / ComplianceChecker / QualityChecker 发现问题时，
本模块负责将问题转化为结构化的经验规则，存储到 ConsistencyLessonStore 中。

两种提取模式：
1. 规则模式（默认）：基于 issue_type 的内置规则模板，无需 LLM 调用
2. LLM 模式（可选）：当 llm_fn 提供时，用 LLM 从复杂问题中提取更深层的规则
"""

import json
import logging
import re
from typing import Any, Callable

from core.evolution.consistency_lesson_store import ConsistencyLessonStore

logger = logging.getLogger(__name__)

# ── 内置规则模板：issue_type → 经验生成规则 ──────────────────────────

_RULE_TEMPLATES: dict[str, dict[str, Any]] = {
    "mutual_exclusion": {
        "title_template": "提交/处理方式互斥：{detail}",
        "rule": "标书中存在多种互斥的提交/处理方式，在同一章节中只能选择一种，严禁混用。"
                "如招标文件已明确指定方式，严格按招标文件要求输出；如未明确，输出选择说明让投标人选择，"
                "然后分别列出各方式的模板，标注删除其他方式。",
        "keywords": ["互斥", "保函", "转账", "电汇", "包干", "单价", "保证金"],
        "directive": "同一章节中不得同时出现两种互斥方式的格式要素。",
    },
    "number_mismatch": {
        "title_template": "跨章节数量不一致：{detail}",
        "rule": "不同章节中出现的同类数量数据必须保持一致。人员数量、设备数量等关键数据"
                "在投标函、服务方案、人员配置等章节中必须完全相同。",
        "keywords": ["人员", "数量", "人", "名", "台", "辆", "不一致"],
        "directive": "生成各章节时须交叉核对关键数量数据，确保全文一致。",
    },
    "amount_mismatch": {
        "title_template": "跨章节金额不一致：{detail}",
        "rule": "投标报价、保证金金额等关键财务数据在不同章节中必须完全一致。"
                "投标函中的报价金额必须与分项报价表合计一致。",
        "keywords": ["金额", "报价", "保证金", "万元", "元", "¥", "不一致"],
        "directive": "所有涉及金额的章节须以投标函报价为基准，分项报价表合计必须等于投标总价。",
    },
    "date_mismatch": {
        "title_template": "跨章节日期不一致：{detail}",
        "rule": "合同签订日期、项目启动日期、投标有效期等关键日期在不同章节中必须一致。",
        "keywords": ["日期", "签订", "开工", "启动", "有效期", "不一致"],
        "directive": "关键日期在各章节中须使用同一值，以项目上下文契约为准。",
    },
    "document_composition_mismatch": {
        "title_template": "投标函声明组成与实际章节不符：{detail}",
        "rule": "投标函中声明的文件组成清单必须与实际提交的章节一一对应。"
                "声明的每一项都必须有对应的章节内容，未声明的章节不得出现在文档中。",
        "keywords": ["文件组成", "投标函", "声明", "分项报价表", "偏差表", "资格审查"],
        "directive": "投标函声明的文件组成清单中每一项必须有对应的实际章节。",
    },
    "tech_parameter_mismatch": {
        "title_template": "跨章节技术参数不一致：{detail}",
        "rule": "技术参数（响应时间、服务时间、到达现场、故障恢复、保修期等）在不同章节中"
                "必须保持一致。技术方案章与服务方案章的技术参数值必须完全相同。",
        "keywords": ["技术参数", "响应时间", "服务时间", "到达现场", "故障恢复", "保修期", "不一致"],
        "directive": "生成各章节时须交叉核对技术参数（响应时间、服务时间等），确保全文一致。",
    },
    "contract_deviation": {
        "title_template": "契约偏离/拼凑残留：{detail}",
        "rule": "章节内容中不得出现项目上下文契约禁止的机构名或行业属性。"
                "拼凑残留（其他项目的机构名/行业属性错误出现）会导致废标，"
                "生成时必须严格检查是否包含契约禁止项。",
        "keywords": ["契约偏离", "拼凑残留", "禁止机构", "禁止行业", "机构名", "行业属性"],
        "directive": "生成各章节时须严格检查是否包含契约禁止的机构名和行业属性，发现则删除。",
    },
    "format_violation": {
        "title_template": "格式合规问题：{detail}",
        "rule": "标书格式必须符合招标文件要求，包括字体、字号、页边距、装订方式等。",
        "keywords": ["格式", "字体", "字号", "页边距", "装订"],
        "directive": "严格按照招标文件格式要求输出。",
    },
    "legal_violation": {
        "title_template": "法律合规问题：{detail}",
        "rule": "标书中不得使用绝对化用语（如「全国第一」「唯一」「最佳」等），"
                "不得编造虚假业绩或资质信息，不得有违反广告法和招标投标法的内容。",
        "keywords": ["绝对化", "最", "唯一", "第一", "虚假", "业绩"],
        "directive": "禁止使用绝对化用语，禁止编造业绩，所有宣传性表述须可验证。",
    },
}

# ── LLM 提取 prompt ──────────────────────────────────────────────────

_LLM_EXTRACT_PROMPT = """你是一个标书一致性专家。请从以下一致性问题中提取一条可复用的规则。

问题类型：{issue_type}
问题描述：{detail}
涉及章节：{sections}

请以 JSON 格式返回，包含以下字段：
- title: 简短标题（不超过20字）
- description: 问题描述（1-2句话）
- rule: 可复用规则（祈使句，说明应该怎么做）
- keywords: 关键词列表（用于后续匹配，3-5个词）
- directive_text: 注入到 prompt 中的指令文本（1-3句话）

只返回 JSON，不要其他内容。
"""


class ConsistencyLessonExtractor:
    """从一致性问题中提取结构化经验。"""

    def __init__(
        self,
        store: ConsistencyLessonStore | None = None,
        llm_fn: Callable[[str], str] | None = None,
    ):
        self.store = store or ConsistencyLessonStore()
        self.llm_fn = llm_fn

    def extract_from_cross_ref(self, inconsistencies: list[dict], bid_type: str = "") -> list[str]:
        """从 CrossReferenceChecker 的 inconsistencies 列表提取经验。

        Returns:
            新增/更新的经验 ID 列表。
        """
        lesson_ids: list[str] = []
        for inc in inconsistencies:
            issue_type = self._map_cross_ref_type(inc.get("type", ""))
            detail = inc.get("detail", "")
            sections = inc.get("sections", [])
            lesson_id = self._extract_one(issue_type, detail, sections, bid_type)
            if lesson_id:
                lesson_ids.append(lesson_id)
        return lesson_ids

    def extract_from_compliance(
        self,
        format_issues: list[dict],
        legal_issues: list[dict],
        bid_type: str = "",
    ) -> list[str]:
        """从 ComplianceChecker 的问题列表提取经验。"""
        lesson_ids: list[str] = []
        for fi in format_issues:
            detail = fi.get("detail", str(fi))
            lesson_id = self._extract_one("format_violation", detail, [], bid_type)
            if lesson_id:
                lesson_ids.append(lesson_id)
        for li in legal_issues:
            detail = li.get("detail", str(li))
            lesson_id = self._extract_one("legal_violation", detail, [], bid_type)
            if lesson_id:
                lesson_ids.append(lesson_id)
        return lesson_ids

    def extract_from_quality(self, quality_report: dict, bid_type: str = "",
                             sections: dict[str, str] | None = None) -> list[str]:
        """从 QualityChecker 的 quality_report 提取一致性经验。

        Args:
            quality_report: QualityChecker 的报告，consistency 为 list[str] 或 list[dict]。
            bid_type: 标书类型。
            sections: 当前所有章节 dict（章节名→内容），用于从问题文本中提取关联章节。
        """
        lesson_ids: list[str] = []
        section_names = list(sections.keys()) if sections else []
        for issue in quality_report.get("consistency", []):
            if isinstance(issue, str):
                detail = issue
            elif isinstance(issue, dict):
                detail = issue.get("detail", str(issue))
            else:
                detail = str(issue)
            # 智能推断 issue_type 而非硬编码 mutual_exclusion
            issue_type = self._classify_quality_issue(detail)
            # 从问题文本中提取关联章节名
            related_sections = self._extract_sections_from_text(detail, section_names)
            lesson_id = self._extract_one(issue_type, detail, related_sections, bid_type)
            if lesson_id:
                lesson_ids.append(lesson_id)
        return lesson_ids

    @staticmethod
    def _classify_quality_issue(detail: str) -> str:
        """根据问题文本推断 issue_type，而非硬编码为 mutual_exclusion。"""
        # 金额相关
        if any(kw in detail for kw in ["金额", "报价", "元", "万元", "¥"]):
            return "amount_mismatch"
        # 数量相关
        if any(kw in detail for kw in ["人数", "数量", "人员", "名", "台", "辆"]):
            return "number_mismatch"
        # 日期相关
        if any(kw in detail for kw in ["日期", "有效期", "签订", "开工", "启动"]):
            return "date_mismatch"
        # 文件组成相关
        if any(kw in detail for kw in ["文件组成", "声明", "缺少", "遗漏"]):
            return "document_composition_mismatch"
        # 互斥方式相关
        if any(kw in detail for kw in ["互斥", "保函", "转账", "电汇", "包干", "单价"]):
            return "mutual_exclusion"
        # 技术参数相关
        if any(kw in detail for kw in ["技术参数", "响应时间", "服务时间", "到达现场", "故障恢复", "保修期"]):
            return "tech_parameter_mismatch"
        # 契约偏离/拼凑残留
        if any(kw in detail for kw in ["契约", "禁止机构", "拼凑", "残留", "禁止行业"]):
            return "contract_deviation"
        # 默认：通用一致性
        return "mutual_exclusion"

    @staticmethod
    def _extract_sections_from_text(text: str, section_names: list[str]) -> list[str]:
        """从问题文本中提取出现的章节名，使经验能被 get_lessons_for_section 匹配。"""
        found = []
        for name in section_names:
            if name in text:
                found.append(name)
        return found

    @staticmethod
    def _map_cross_ref_type(cross_ref_type: str) -> str:
        """将 CrossReferenceChecker 的 type 映射到经验 issue_type。

        H11 fix: 补全 tech_parameter_mismatch / contract_forbidden_institution /
        contract_forbidden_industry 三类映射，之前全部 fallback 到 mutual_exclusion，
        导致 CRITICAL 级契约偏离被错归为互斥问题。
        """
        mapping = {
            "number_mismatch": "number_mismatch",
            "amount_mismatch": "amount_mismatch",
            "date_mismatch": "date_mismatch",
            "tech_parameter_mismatch": "tech_parameter_mismatch",
            "document_composition_mismatch": "document_composition_mismatch",
            "contract_forbidden_institution": "contract_deviation",
            "contract_forbidden_industry": "contract_deviation",
        }
        return mapping.get(cross_ref_type, "mutual_exclusion")

    def _extract_one(
        self,
        issue_type: str,
        detail: str,
        sections: list[str],
        bid_type: str = "",
    ) -> str | None:
        """提取单条经验并存储。"""
        if not detail or not detail.strip():
            return None

        # 尝试 LLM 提取（如果可用）
        if self.llm_fn is not None:
            try:
                return self._extract_with_llm(issue_type, detail, sections, bid_type)
            except Exception as e:
                logger.warning(f"LLM extraction failed, falling back to rule template: {e}")

        # 规则模板提取（默认）
        return self._extract_with_template(issue_type, detail, sections, bid_type)

    def _extract_with_template(
        self,
        issue_type: str,
        detail: str,
        sections: list[str],
        bid_type: str = "",
    ) -> str | None:
        """使用内置规则模板提取经验。"""
        template = _RULE_TEMPLATES.get(issue_type)
        if not template:
            logger.warning(f"No rule template for issue_type: {issue_type}")
            return None

        title = template["title_template"].format(detail=detail[:50])
        rule = template["rule"]
        keywords = list(template["keywords"])
        directive = template["directive"]

        # 仅使用模板预定义的 keywords，不再从 detail 中自动提取中文子串。
        # 原来的 re.findall(r'[\u4e00-\u9fff]{2,6}', detail) 会产生
        # 无意义子串（如「投标函报价与分」），污染 keyword 匹配。

        return self.store.add_lesson(
            issue_type=issue_type,
            title=title,
            description=detail,
            rule=rule,
            keywords=keywords[:10],
            applicable_sections=sections,
            applicable_bid_types=[bid_type] if bid_type else [],
            directive_text=directive,
        )

    def _extract_with_llm(
        self,
        issue_type: str,
        detail: str,
        sections: list[str],
        bid_type: str = "",
    ) -> str | None:
        """使用 LLM 从复杂问题中提取更深层的经验规则。"""
        prompt = _LLM_EXTRACT_PROMPT.format(
            issue_type=issue_type,
            detail=detail,
            sections="、".join(sections) if sections else "未指定",
        )
        response = self.llm_fn(prompt)
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                parsed = json.loads(match.group())
            else:
                logger.warning(f"LLM returned non-JSON: {response[:100]}")
                return self._extract_with_template(issue_type, detail, sections, bid_type)

        return self.store.add_lesson(
            issue_type=issue_type,
            title=parsed.get("title", f"{issue_type}: {detail[:30]}"),
            description=parsed.get("description", detail),
            rule=parsed.get("rule", detail),
            keywords=parsed.get("keywords", []),
            applicable_sections=sections,
            applicable_bid_types=[bid_type] if bid_type else [],
            directive_text=parsed.get("directive_text", parsed.get("rule", "")),
        )

    def learn_from_state(self, state: dict, bid_type: str = "") -> list[str]:
        """从 AgentState 中提取所有一致性问题并学习经验。

        一次性处理 cross_ref_report、compliance_report、quality_report。
        """
        all_ids: list[str] = []

        # Cross-reference issues
        cross_ref = state.get("cross_ref_report", {})
        if cross_ref.get("inconsistencies"):
            ids = self.extract_from_cross_ref(cross_ref["inconsistencies"], bid_type)
            all_ids.extend(ids)

        # Compliance issues
        compliance = state.get("compliance_report", {})
        fmt_issues = compliance.get("format_issues", [])
        legal_issues = compliance.get("legal_issues", [])
        if fmt_issues or legal_issues:
            ids = self.extract_from_compliance(fmt_issues, legal_issues, bid_type)
            all_ids.extend(ids)

        # Quality report consistency issues
        quality = state.get("quality_report", {})
        if quality.get("consistency"):
            sections = state.get("sections", {})
            ids = self.extract_from_quality(quality, bid_type, sections)
            all_ids.extend(ids)

        if all_ids:
            logger.info(f"Learned {len(all_ids)} consistency lessons from state")
        return all_ids
