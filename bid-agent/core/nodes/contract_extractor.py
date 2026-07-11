"""ContractExtractor node — LLM 改写补充说明 + 提取结构化字段 → 构建项目上下文契约.

管道位置:
    DocumentParser → ReqExtractor → ContractExtractor → EligibilityChecker → ...

工作流:
    1. 从 state.extra_reqs 获取用户补充说明 (自由文本)
    2. 调用 LLM 提取结构化字段 (投标人/项目地点/行业/工期等) + 改写策略性要求
    3. 三方合并: 表单字段 > 招标文件提取 > LLM提取
    4. 构建 ProjectContextContract, 存入 state.project_contract
    5. 推断禁止项 (forbidden_institutions / forbidden_industries)

Contract:
    def contract_extractor(state: AgentState, llm_fn=None) -> dict
    Input:  state.extra_reqs, state.requirements, state.bid_type
    Output: {project_contract: dict, global_constraints: [str], node_status}
"""

import json
import logging
import re
from typing import Callable

from core.state import AgentState, NodeStatus
from core.contract import ProjectContextContract

logger = logging.getLogger(__name__)


# ============================================================
# LLM Prompt — 改写 + 提取
# ============================================================

_CONTRACT_EXTRACTION_PROMPT = """你是一个招投标信息提取专家。请从用户补充说明中提取项目结构化信息，并将剩余策略性要求改写为规范的项目说明。

【用户补充说明】
{extra_reqs}

【招标文件已提取的信息（供参考交叉验证）】
{requirements_summary}

请以严格 JSON 格式返回（不要包含任何其他文字）：
{{
  "project_name": "项目名称（明确提及则提取，否则留空）",
  "bidder_name": "投标人/投标公司全称（明确提及则提取）",
  "tenderer_name": "招标人/采购人名称（明确提及则提取）",
  "project_location": "项目地点或服务地域",
  "industry": "行业属性（如：物业服务/人力资源/教育校园/信息技术/工程建设/运维服务）",
  "duration": "工期或服务期（如：12个月）",
  "warranty": "质保期（如：3年）",
  "service_target": "服务对象",
  "rewritten_notes": "改写后的补充说明：去除已提取的事实信息，只保留策略性要求，语言规范专业"
}}

提取规则：
1. 只提取明确提及的信息，不可猜测编造
2. 无法提取的字段留空字符串 ""
3. rewritten_notes 去掉已提取的事实（如公司名、地点），只保留策略指导（如"重点突出本地化服务能力"）
4. 补充说明为空时，所有字段返回空字符串
5. 如果招标文件已有项目名称等信息，与补充说明交叉验证，以更完整的为准"""


# ============================================================
# JSON 解析 (复用 req_extractor 的模式)
# ============================================================

def _extract_json_from_response(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON."""
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# 辅助函数
# ============================================================

def _build_requirements_summary(requirements: dict) -> str:
    """从 requirements 构建简要摘要供 LLM 交叉验证."""
    parts = []
    scoring = requirements.get("scoring", [])
    if scoring:
        parts.append(f"评分项: {len(scoring)} 项")
    quals = requirements.get("qualifications", [])
    if quals:
        parts.append(f"资质要求: {len(quals)} 项")
    tech = requirements.get("tech_specs", [])
    if tech:
        parts.append(f"技术规格: {len(tech)} 项")
    return "; ".join(parts) if parts else "无"


def _build_mock_extraction(extra_reqs: str) -> dict:
    """无 LLM 时的简单提取 — 用正则尝试提取公司名等基本信息."""
    result = {
        "project_name": "",
        "bidder_name": "",
        "tenderer_name": "",
        "project_location": "",
        "industry": "",
        "duration": "",
        "warranty": "",
        "service_target": "",
        "rewritten_notes": extra_reqs,
    }

    if not extra_reqs:
        return result

    # 尝试提取公司名
    company_patterns = [
        re.compile(r"(?:投标公司|投标人|投标方|公司名称)\s*[为是：:]\s*([^\s,，。；;]{4,30})"),
        re.compile(r"([\u4e00-\u9fa5]{2,10}(?:有限公司|股份有限公司|有限责任公司|集团有限公司))"),
    ]
    for pat in company_patterns:
        m = pat.search(extra_reqs)
        if m:
            result["bidder_name"] = m.group(1).strip()
            break

    # 尝试提取地点
    cities = ["北京", "上海", "广州", "深圳", "长春", "天津", "重庆",
              "成都", "杭州", "南京", "武汉", "西安", "长沙", "郑州"]
    for city in cities:
        if city in extra_reqs:
            result["project_location"] = city
            break

    # 尝试提取工期
    dur_match = re.search(r"(\d+)\s*(个?月|年|天|日)", extra_reqs)
    if dur_match:
        result["duration"] = f"{dur_match.group(1)}{dur_match.group(2)}"

    return result


# ============================================================
# 三方合并
# ============================================================

def _confirmed_or_fallback(
    confirmed: dict, key: str, *fallbacks: str
) -> str:
    """返回用户确认值；若 key 不在 confirmed 中则取首个非空回退值。

    与 ``confirmed.get(key) or fallback`` 的关键区别：
    - key 存在且值为空字符串 ``""`` → 返回 ``""``（用户显式清空）
    - key 不存在 → 返回首个非空回退值

    这确保用户在校验表单中清空一个字段时，不会被回退值覆盖。
    """
    if key in confirmed:
        return confirmed[key]
    for f in fallbacks:
        if f:
            return f
    return ""


def _merge_contract_sources(
    llm_extracted: dict,
    requirements: dict,
    bid_type: str,
    user_confirmed_fields: dict | None = None,
) -> ProjectContextContract:
    """四方合并构建契约.

    优先级: 用户校验确认 > 招标文件提取 > LLM从补充说明提取

    Args:
        llm_extracted: LLM 从补充说明提取的结构化字段
        requirements: ReqExtractor 从招标文件提取的需求
        bid_type: 用户选择的标书类型
        user_confirmed_fields: 用户在 InfoVerificationGate 校验后确认/修改的字段
            (最高优先级, 覆盖所有其他来源)
    """
    confirmed = user_confirmed_fields or {}

    # 从 requirements 提取可能的项目名/招标人/招标编号 (ReqExtractor 提取)
    req_project_name = requirements.get("project_name", "")
    req_tenderer = requirements.get("tenderer_name", "")
    req_bid_number = requirements.get("bid_number", "")
    req_package_number = requirements.get("package_number", "")

    # 行业推断: bid_type 可以补充 industry
    type_to_industry = {
        "服务类": "服务",
        "货物类": "货物采购",
        "软件类": "信息技术",
        "工程类": "工程建设",
        "集成类": "信息技术",
        "运维类": "运维服务",
        "劳务管理服务类": "人力资源",
        "劳务外包类": "人力资源",
    }
    type_industry = type_to_industry.get(bid_type, "")

    # 逐字段合并 (优先级: 用户校验确认 > 招标文件提取 > LLM从补充说明提取)
    # 使用 _confirmed_or_fallback 确保用户显式清空的字段不会被回退值覆盖
    contract = ProjectContextContract(
        project_name=_confirmed_or_fallback(
            confirmed, "project_name",
            req_project_name,
            llm_extracted.get("project_name", ""),
        ),
        project_code=(
            confirmed["bid_number"] if "bid_number" in confirmed
            else confirmed.get("project_code", "") if "project_code" in confirmed
            else req_bid_number or llm_extracted.get("project_code", "")
        ),
        package_number=_confirmed_or_fallback(
            confirmed, "package_number",
            req_package_number,
            llm_extracted.get("package_number", ""),
        ),
        bidder_name=_confirmed_or_fallback(
            confirmed, "bidder_name",
            llm_extracted.get("bidder_name", ""),
        ),
        tenderer_name=_confirmed_or_fallback(
            confirmed, "tenderer_name",
            req_tenderer,
            llm_extracted.get("tenderer_name", ""),
        ),
        project_location=_confirmed_or_fallback(
            confirmed, "project_location",
            llm_extracted.get("project_location", ""),
        ),
        industry=_confirmed_or_fallback(
            confirmed, "industry",
            llm_extracted.get("industry", ""),
            type_industry,
        ),
        duration=_confirmed_or_fallback(
            confirmed, "duration",
            llm_extracted.get("duration", ""),
        ),
        warranty=_confirmed_or_fallback(
            confirmed, "warranty",
            llm_extracted.get("warranty", ""),
        ),
        service_target=_confirmed_or_fallback(
            confirmed, "service_target",
            llm_extracted.get("service_target", ""),
        ),
        notes=llm_extracted.get("rewritten_notes", ""),
    )

    # 推断禁止项
    if contract.project_name or contract.bidder_name:
        contract.infer_forbidden()

    return contract


# ============================================================
# LangGraph Node
# ============================================================

def contract_extractor(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """从补充说明 + 招标文件提取项目上下文契约.

    Args:
        state: AgentState with extra_reqs, requirements, bid_type
        llm_fn: Optional LLM callable (prompt) -> text

    Returns:
        {project_contract: dict, global_constraints: [str], node_status}
    """
    extra_reqs = state.get("extra_reqs", "")
    requirements = state.get("requirements", {})
    bid_type = state.get("bid_type", "")
    user_confirmed = state.get("user_confirmed_fields", {})

    # ── 无补充说明时, 仍尝试从 requirements 构建 minimal 契约 ──────────
    if not extra_reqs:
        logger.info("ContractExtractor: 无补充说明, 构建最小契约")
        contract = _merge_contract_sources(
            llm_extracted={},
            requirements=requirements,
            bid_type=bid_type,
            user_confirmed_fields=user_confirmed,
        )
        ok, errors = contract.is_valid()
        if not ok:
            logger.warning(f"ContractExtractor: 契约不完整 (无补充说明): {errors}")
        return {
            "project_contract": contract.to_dict(),
            "global_constraints": [],
            "node_status": {
                **state.get("node_status", {}),
                "ContractExtractor": NodeStatus.COMPLETED.value,
            },
        }

    # ── 有补充说明: 调用 LLM 提取 ──────────────────────────────────────
    req_summary = _build_requirements_summary(requirements)
    prompt = _CONTRACT_EXTRACTION_PROMPT.format(
        extra_reqs=extra_reqs,
        requirements_summary=req_summary,
    )

    if llm_fn is None:
        from core.graph import get_pipeline_llm
        global_llm = get_pipeline_llm()
        if global_llm is not None:
            llm_fn = global_llm

    if llm_fn is not None:
        try:
            response = llm_fn(prompt)
            extracted = _extract_json_from_response(response)
            if extracted is None:
                logger.warning("ContractExtractor: LLM 返回非 JSON, 回退到正则提取")
                extracted = _build_mock_extraction(extra_reqs)
        except Exception as e:
            logger.error(f"ContractExtractor: LLM 调用失败: {e}, 回退到正则提取")
            extracted = _build_mock_extraction(extra_reqs)
    else:
        logger.info("ContractExtractor: 无 LLM, 使用正则提取")
        extracted = _build_mock_extraction(extra_reqs)

    # ── 四方合并 ───────────────────────────────────────────────────────
    contract = _merge_contract_sources(
        llm_extracted=extracted,
        requirements=requirements,
        bid_type=bid_type,
        user_confirmed_fields=user_confirmed,
    )

    ok, errors = contract.is_valid()
    if ok:
        logger.info(
            f"ContractExtractor: 契约构建成功 — "
            f"项目={contract.project_name[:30]}, "
            f"投标人={contract.bidder_name}, "
            f"地点={contract.project_location}, "
            f"行业={contract.industry}"
        )
    else:
        logger.warning(f"ContractExtractor: 契约不完整: {errors}")

    # ── 改写后的 notes 同时作为 global_constraints ─────────────────────
    global_constraints = []
    if contract.notes:
        global_constraints.append(contract.notes)

    return {
        "project_contract": contract.to_dict(),
        "global_constraints": global_constraints,
        "node_status": {
            **state.get("node_status", {}),
            "ContractExtractor": NodeStatus.COMPLETED.value,
        },
    }
