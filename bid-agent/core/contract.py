"""Project Context Contract — 标书全篇不可变字段契约.

确保并行生成各章节时项目名称/投标人/项目地点/行业属性等关键字段一致.

工作流:
  1. ContractExtractor 节点从补充说明(LLM提取) + 招标文件(ReqExtractor) 合并构建契约
  2. SectionGenerator 每章 prompt 注入 contract.summary() 作为强制上下文
  3. CrossReferenceChecker 用 ContractValidator 校验全篇, 偏离打回重生

契约字段优先级:
  表单字段(用户显式填写) > 招标文件提取 > LLM从补充说明提取
"""

from __future__ import annotations

import json
import re
import difflib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# ============================================================
# 契约数据结构
# ============================================================

@dataclass
class ProjectContextContract:
    """项目上下文契约 — 标书全篇不可变字段.

    任何章节生成时必须遵循此契约, 偏离即 CRITICAL.
    """

    # === 必填字段 (缺失则契约本身不成立) ===
    project_name: str = ""
    bidder_name: str = ""

    # === 强烈建议字段 ===
    project_code: str = ""
    package_number: str = ""
    project_location: str = ""
    industry: str = ""
    service_target: str = ""
    duration: str = ""
    warranty: str = ""
    tenderer_name: str = ""

    # === 元数据 ===
    created_at: str = ""
    created_by: str = "contract_extractor"
    version: str = "1.0.0"
    notes: str = ""

    # === 禁止出现项 (拼凑残留信号) ===
    forbidden_institutions: List[str] = field(default_factory=list)
    forbidden_industries: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")

    def is_valid(self) -> Tuple[bool, List[str]]:
        """契约本身是否成立 (必填字段检查)."""
        errors = []
        if not self.project_name:
            errors.append("project_name 未填写")
        if not self.bidder_name:
            errors.append("bidder_name 未填写")
        return (len(errors) == 0, errors)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectContextContract":
        """从 dict 构建, 忽略未知字段."""
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "ProjectContextContract":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def summary(self) -> str:
        """人读摘要, 供章节 agent 作为 prompt 上下文."""
        lines = [
            f"项目名称: {self.project_name}",
            f"项目编号: {self.project_code or '(未指定)'}",
            f"包件号: {self.package_number or '(未指定)'}",
            f"投标人: {self.bidder_name}",
            f"招标人: {self.tenderer_name or '(未指定)'}",
            f"项目地点: {self.project_location or '(未指定)'}",
            f"行业属性: {self.industry or '(未指定)'}",
            f"服务对象: {self.service_target or '(未指定)'}",
            f"工期/服务期: {self.duration or '(未指定)'}",
            f"质保期: {self.warranty or '(未指定)'}",
        ]
        if self.notes:
            lines.append(f"补充要求: {self.notes}")
        if self.forbidden_institutions:
            lines.append(f"禁止出现机构: {', '.join(self.forbidden_institutions)}")
        if self.forbidden_industries:
            lines.append(f"禁止涉及行业: {', '.join(self.forbidden_industries)}")
        return "\n".join(lines)

    def infer_forbidden(self) -> None:
        """根据契约行业/服务对象推断禁止项 (拼凑残留检测)."""
        all_insts = [
            "复旦大学", "清华大学", "北京大学", "中央民族大学",
            "中国科学院长春应用化学研究所", "腾讯", "阿里", "华为",
        ]
        allowed = self.service_target + self.project_name + self.tenderer_name + self.bidder_name
        self.forbidden_institutions = [
            inst for inst in all_insts if inst not in allowed
        ]

        all_inds = ["物业服务", "化学研发", "教育校园", "信息技术",
                     "医疗卫生", "工程建设", "人力资源"]
        # 行业可能是复合属性（如"教育校园/信息技术"），拆分后逐个匹配
        industry_parts = [p.strip() for p in self.industry.split("/") if p.strip()]
        self.forbidden_industries = [
            ind for ind in all_inds
            if ind not in self.industry
            and self.industry not in ind
            and ind not in industry_parts
        ]


# ============================================================
# 契约校验器
# ============================================================

@dataclass
class ContractIssue:
    """契约偏离问题."""
    severity: str           # critical | warning
    category: str
    chapter: str
    message: str
    evidence: str = ""
    contract_value: str = ""
    found_value: str = ""


@dataclass
class ContractReport:
    """契约校验综合报告."""
    contract: ProjectContextContract
    chapter_issues: Dict[str, List[ContractIssue]] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for issues in self.chapter_issues.values()
                   for i in issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for issues in self.chapter_issues.values()
                   for i in issues if i.severity == "warning")

    @property
    def exit_code(self) -> int:
        if self.critical_count > 0:
            return 2
        if self.warning_count > 0:
            return 1
        return 0

    def to_dict(self) -> dict:
        return {
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "exit_code": self.exit_code,
            "chapter_issues": {
                ch: [asdict(i) for i in issues]
                for ch, issues in self.chapter_issues.items()
            },
        }


class ContractValidator:
    """校验章节内容是否与契约一致.

    六类校验:
      1. 项目名称一致性
      2. 投标人名称一致性
      3. 项目地点一致性
      4. 行业属性一致性
      5. 禁止机构检测
      6. 时间周期一致性
    """

    PROJECT_NAME_PATTERNS = [
        re.compile(r"(?:项目名称|项目名|采购项目|工程名称)\s*[:：]\s*([^\n]{5,80})"),
        re.compile(r"(?:项目编号|招标编号|采购编号)\s*[:：]\s*([A-Za-z0-9\-]{4,40})"),
    ]
    BIDDER_NAME_PATTERNS = [
        re.compile(r"(?:投标人|供应商|投标方|投标人名称|公司名称)\s*[:：]\s*([^\n]{4,60})"),
    ]

    LOCATION_KEYWORDS = [
        "北京", "上海", "广州", "深圳", "长春", "天津", "重庆",
        "成都", "杭州", "南京", "武汉", "西安", "长沙", "郑州",
    ]

    DURATION_PATTERN = re.compile(r"(\d+)\s*(个?月|年|天|日)")
    WARRANTY_PATTERN = re.compile(r"质保期\s*[:：]?\s*(\d+)\s*(个?月|年|天|日)")

    def __init__(self, contract: ProjectContextContract):
        self.contract = contract
        # 契约不完整时只做警告, 不阻断管道
        ok, _ = contract.is_valid()
        if not ok:
            import logging
            logging.getLogger(__name__).warning(
                "契约不完整, 校验将跳过必填字段检查"
            )

    def _check_project_name(self, chapter: str, text: str) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        expected = self.contract.project_name
        if not expected:
            return issues

        found_names: List[str] = []
        for pat in self.PROJECT_NAME_PATTERNS:
            for m in pat.finditer(text):
                name = m.group(1).strip()
                if len(name) >= 5 and name not in found_names:
                    found_names.append(name)

        for found in found_names:
            ratio = difflib.SequenceMatcher(None, found, expected).ratio()
            if ratio < 0.6:
                issues.append(ContractIssue(
                    severity="critical",
                    category="contract_project_name_mismatch",
                    chapter=chapter,
                    message=f"项目名称与契约不一致: 章节='{found[:40]}' vs 契约='{expected[:40]}'",
                    evidence=f"相似度 {ratio:.0%}",
                    contract_value=expected,
                    found_value=found,
                ))
            elif ratio < 0.85:
                issues.append(ContractIssue(
                    severity="warning",
                    category="contract_project_name_variant",
                    chapter=chapter,
                    message="项目名称与契约存在差异 (可能是简写/全称)",
                    evidence=f"章节='{found[:40]}' vs 契约='{expected[:40]}' (相似度 {ratio:.0%})",
                    contract_value=expected,
                    found_value=found,
                ))
        return issues

    def _check_bidder_name(self, chapter: str, text: str) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        expected = self.contract.bidder_name
        if not expected:
            return issues

        found_names: List[str] = []
        for pat in self.BIDDER_NAME_PATTERNS:
            for m in pat.finditer(text):
                name = m.group(1).strip()
                if len(name) >= 4 and name not in found_names:
                    found_names.append(name)

        for found in found_names:
            ratio = difflib.SequenceMatcher(None, found, expected).ratio()
            if ratio < 0.5:
                issues.append(ContractIssue(
                    severity="critical",
                    category="contract_bidder_mismatch",
                    chapter=chapter,
                    message=f"投标人名称与契约不一致: 章节='{found}' vs 契约='{expected}'",
                    evidence=f"相似度 {ratio:.0%}",
                    contract_value=expected,
                    found_value=found,
                ))
        return issues

    def _check_location(self, chapter: str, text: str) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        expected_loc = self.contract.project_location
        if not expected_loc:
            return issues

        exempt = self.contract.service_target + self.contract.tenderer_name
        found_locs = [loc for loc in self.LOCATION_KEYWORDS
                      if loc in text and loc not in exempt]

        for loc in found_locs:
            if loc != expected_loc and loc not in expected_loc:
                if expected_loc in loc or loc in expected_loc:
                    continue
                issues.append(ContractIssue(
                    severity="warning",
                    category="contract_location_conflict",
                    chapter=chapter,
                    message=f"项目地点可能与契约冲突: 章节出现'{loc}', 契约地点='{expected_loc}'",
                    evidence=f"章节地点: {loc}",
                    contract_value=expected_loc,
                    found_value=loc,
                ))
        return issues

    def _check_industry(self, chapter: str, text: str,
                        industry_keywords: Optional[Dict] = None) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        expected_ind = self.contract.industry
        if not expected_ind:
            return issues

        if industry_keywords is None:
            industry_keywords = self._default_industry_keywords()

        found_industries: Dict[str, List[str]] = {}
        for ind, kws in industry_keywords.items():
            hit = [kw for kw in kws if kw in text]
            if len(hit) >= 2:
                found_industries[ind] = hit

        expected_matched = any(expected_ind in ind or ind in expected_ind
                               for ind in found_industries)

        for forbidden_ind in self.contract.forbidden_industries:
            for ind in found_industries:
                if forbidden_ind in ind or ind in forbidden_ind:
                    issues.append(ContractIssue(
                        severity="critical",
                        category="contract_forbidden_industry",
                        chapter=chapter,
                        message=f"章节涉及契约禁止的行业 '{ind}' (契约行业: {expected_ind})",
                        evidence=f"命中关键词: {found_industries[ind][:5]}",
                        contract_value=expected_ind,
                        found_value=ind,
                    ))

        if not expected_matched and found_industries:
            for ind in found_industries:
                if ind not in expected_ind and expected_ind not in ind:
                    issues.append(ContractIssue(
                        severity="critical",
                        category="contract_industry_mismatch",
                        chapter=chapter,
                        message=f"章节行业属性与契约不一致: 章节='{ind}' vs 契约='{expected_ind}'",
                        evidence=f"命中关键词: {found_industries[ind][:5]}",
                        contract_value=expected_ind,
                        found_value=ind,
                    ))
        return issues

    def _default_industry_keywords(self) -> Dict[str, List[str]]:
        return {
            "物业服务": ["物业", "保洁", "保安", "绿化", "楼宇管理", "秩序维护"],
            "化学研发": ["化学", "试剂", "合成", "纯化", "催化", "公斤级", "显示材料"],
            "教育校园": ["校园", "教学", "师生", "教务", "学籍", "课程", "智慧校园"],
            "信息技术": ["门户", "SaaS", "云平台", "微服务", "API网关", "中台", "数字化"],
            "医疗卫生": ["医疗", "医院", "临床", "诊疗", "药品", "器械"],
            "工程建设": ["施工", "土建", "安装", "总承包", "监理", "竣工"],
            "人力资源": ["人力", "劳务", "外包", "派遣", "用工", "薪酬", "社保"],
        }

    def _check_forbidden_institutions(self, chapter: str, text: str) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        for inst in self.contract.forbidden_institutions:
            if inst in text:
                issues.append(ContractIssue(
                    severity="critical",
                    category="contract_forbidden_institution",
                    chapter=chapter,
                    message=f"章节出现契约禁止的机构名 '{inst}' (疑似拼凑残留)",
                    evidence=f"禁止机构: {inst}",
                    contract_value="(禁止出现)",
                    found_value=inst,
                ))
        return issues

    def _check_duration(self, chapter: str, text: str) -> List[ContractIssue]:
        issues: List[ContractIssue] = []
        expected_dur = self.contract.duration
        expected_war = self.contract.warranty

        if expected_dur:
            found_durations = self.DURATION_PATTERN.findall(text)
            if found_durations:
                expected_num = re.search(r"(\d+)", expected_dur)
                if expected_num:
                    exp_val = int(expected_num.group(1))
                    for num_str, unit in found_durations:
                        num = int(num_str)
                        if abs(num - exp_val) > max(exp_val * 0.5, 2):
                            issues.append(ContractIssue(
                                severity="warning",
                                category="contract_duration_mismatch",
                                chapter=chapter,
                                message=f"工期/周期数值可能与契约不一致: 章节={num}{unit}, 契约={expected_dur}",
                                evidence=f"章节: {num}{unit}",
                                contract_value=expected_dur,
                                found_value=f"{num}{unit}",
                            ))

        if expected_war:
            found_wars = self.WARRANTY_PATTERN.findall(text)
            if found_wars:
                expected_num = re.search(r"(\d+)", expected_war)
                if expected_num:
                    exp_val = int(expected_num.group(1))
                    for num_str, unit in found_wars:
                        num = int(num_str)
                        if abs(num - exp_val) > max(exp_val * 0.5, 1):
                            issues.append(ContractIssue(
                                severity="warning",
                                category="contract_warranty_mismatch",
                                chapter=chapter,
                                message=f"质保期数值可能与契约不一致: 章节={num}{unit}, 契约={expected_war}",
                                evidence=f"章节: {num}{unit}",
                                contract_value=expected_war,
                                found_value=f"{num}{unit}",
                            ))
        return issues

    def validate_chapter(self, chapter: str, text: str,
                         industry_keywords: Optional[Dict] = None) -> List[ContractIssue]:
        """校验单章是否与契约一致."""
        all_issues: List[ContractIssue] = []
        all_issues.extend(self._check_project_name(chapter, text))
        all_issues.extend(self._check_bidder_name(chapter, text))
        all_issues.extend(self._check_location(chapter, text))
        all_issues.extend(self._check_industry(chapter, text, industry_keywords))
        all_issues.extend(self._check_forbidden_institutions(chapter, text))
        all_issues.extend(self._check_duration(chapter, text))
        return all_issues

    def validate_document(self, chapters: Dict[str, str]) -> ContractReport:
        """校验全篇."""
        report = ContractReport(contract=self.contract)
        for chapter, text in chapters.items():
            report.chapter_issues[chapter] = self.validate_chapter(chapter, text)
        return report
