"""SubtypeRouter — 劳务外包类子类型识别与检索路由.

95+优化方案 补强一/三/四:
    - 补强一: 三层识别 (关键词 → LLM → 用户确认)
    - 补强三: _shared 严格准入 + 权重控制
    - 补强四: 新子类型完整冷启动链路

三层识别架构:
    Tier 1: 关键词快速匹配 (确定性)
        → 最高命中率 ≥ 0.8 → 直接返回 (高置信)
        → 最高命中率 < 0.8 → 进入 Tier 2
    Tier 2: LLM 辅助判断 (模糊消歧)
        → 前3000字 + 关键词命中矩阵 → LLM
        → confidence ≥ 0.7 → 返回结果
        → confidence < 0.7 → 进入 Tier 3
    Tier 3: 用户确认 (兜底, 由 InfoVerificationGate 处理)
        → 返回 Top-3 候选 + 置信度 + LLM理由

冷启动链路:
    chunk_count = 0  → _shared 0.7 + 最近邻 0.3
    chunk_count 1-4  → 自身 1.0 + _shared 0.3
    chunk_count ≥ 5  → 自身 1.0 (独立运作)
"""

import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# ── 子类型关键词库 ──────────────────────────────────────────────────────

_SUBTYPE_KEYWORDS: dict[str, list[str]] = {
    "食堂餐饮": [
        "食堂", "餐饮", "食材", "供餐", "厨房", "菜单", "营养",
        "食品经营许可", "HACCP", "留样", "食品安全", "供餐服务",
        "餐厨", "膳食", "用餐",
    ],
    "工业生产线": [
        "生产线", "车间", "工厂", "制造", "焊接", "装配", "驻厂",
        "劳务派遣", "生产操作", "工业", "制造工", "技术工人",
        "生产外包", "制造外包",
    ],
    "保安保洁": [
        "保安", "保洁", "安保", "巡逻", "清洁", "绿化", "物业管理",
        "门卫", "秩序维护", "环境卫生", "安保服务", "保洁服务",
    ],
    "仓储物流": [
        "仓储", "物流", "分拣", "装卸", "配送", "仓库", "供应链",
        "货运", "搬运", "库存管理", "物流外包",
    ],
    "HRO": [
        "人力资源", "薪酬代发", "社保代理", "招聘外包", "人事代理",
        "人力资源管理", "劳务管理", "用工管理", "人力资源服务",
        "人才派遣", "劳务管理服务",
    ],
    "BPO": [
        "业务流程", "呼叫中心", "数据处理", "客服外包", "业务外包",
        "业务流程外包", "数据处理外包", "呼叫服务",
    ],
}

# ── _shared 准入白名单: 只有格式固定型章节才可进入 _shared/ ──────────
# 这些章节由法规/模板决定, 不含子类型专有术语, 跨子类型几乎一致

_SHARED_ALLOWED_CHAPTER_KEYS = {
    "sec1_bid_letter",       # 投标函 — 格式固定
    "sec2_legal_rep",        # 法定代表人身份证明 — 格式固定
    "sec3_authorization",    # 授权委托书 — 格式固定
    "sec4_deposit",          # 投标保证金 — 格式固定
    "sec5_deviation",        # 商务和技术偏差表 — 格式固定
    "ch1_letter",            # 通用: 投标函
    "ch2_authorization",     # 通用: 授权委托书
}

# ── 知识库根目录 ────────────────────────────────────────────────────────

_DEFAULT_KB_ROOT = Path(__file__).parent.parent.parent.parent / "knowledge_base"


# ============================================================
# 补强一: 三层识别
# ============================================================

def detect_subtype(
    text: str,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """三层子类型识别: 关键词 → LLM → 用户确认(由调用方处理).

    Args:
        text: 招标文件前N字文本 (建议前3000字)
        llm_fn: 可选的 LLM 调用函数, 用于 Tier 2 消歧

    Returns:
        {
            "bid_subtype": str,          # 识别结果 (可能为空, 等待用户确认)
            "confidence": float,         # 0.0-1.0
            "method": str,               # "keyword" | "llm" | "fallback"
            "candidates": list,          # Top-3 候选 [(subtype, score), ...]
            "llm_reason": str,           # LLM 给出的理由 (仅 Tier 2)
        }
    """
    # Tier 1: 关键词评分
    scores = _keyword_scoring(text)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top_subtype, top_score = ranked[0] if ranked else ("", 0.0)

    if top_score >= 0.8:
        logger.info(
            f"SubtypeRouter: Tier 1 keyword match '{top_subtype}' "
            f"(score={top_score:.2f})"
        )
        return {
            "bid_subtype": top_subtype,
            "confidence": top_score,
            "method": "keyword",
            "candidates": ranked[:3],
            "llm_reason": "",
        }

    # Tier 2: LLM 辅助 (confidence < 0.8 时触发)
    if llm_fn:
        result = _llm_disambiguate(text[:3000], scores, llm_fn)
        if result["confidence"] >= 0.7:
            logger.info(
                f"SubtypeRouter: Tier 2 LLM match '{result['bid_subtype']}' "
                f"(confidence={result['confidence']:.2f}): {result['llm_reason'][:60]}"
            )
            return result

    # Tier 3: 返回 Top-3 候选, 交给 InfoVerificationGate
    logger.info(
        f"SubtypeRouter: Tier 3 fallback, top candidate '{top_subtype}' "
        f"(score={top_score:.2f}), awaiting user confirmation"
    )
    return {
        "bid_subtype": top_subtype,
        "confidence": top_score,
        "method": "fallback",
        "candidates": ranked[:3],
        "llm_reason": "",
    }


def _keyword_scoring(text: str) -> dict[str, float]:
    """计算各子类型关键词命中率.

    Returns:
        {subtype: hit_rate} — hit_rate = 命中关键词数 / 总关键词数
    """
    if not text:
        return {subtype: 0.0 for subtype in _SUBTYPE_KEYWORDS}

    scores: dict[str, float] = {}
    for subtype, keywords in _SUBTYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        scores[subtype] = hits / len(keywords) if keywords else 0.0
    return scores


_SUBTYPE_LLM_PROMPT = """请判断这份招标文件属于以下哪个劳务外包子类型。
如果项目同时涉及多个子类型，请选择"服务范围占比最大"的子类型。

可选子类型: 食堂餐饮、工业生产线、保安保洁、仓储物流、HRO(人力资源外包)、BPO(业务流程外包)

关键词命中统计:
{keyword_scores}

文档前3000字:
{document_text}

请以严格 JSON 格式返回（不要包含任何其他文字）:
{{
  "bid_subtype": "子类型名称",
  "confidence": 0.0到1.0之间的数字,
  "reason": "判断理由（一句话）"
}}"""


def _llm_disambiguate(
    text: str,
    keyword_scores: dict[str, float],
    llm_fn: Callable[[str], str],
) -> dict:
    """Tier 2: 使用 LLM 进行模糊消歧."""
    import re

    scores_str = "\n".join(
        f"  {subtype}: {score:.2f}"
        for subtype, score in sorted(keyword_scores.items(), key=lambda x: -x[1])
    )
    prompt = _SUBTYPE_LLM_PROMPT.format(
        keyword_scores=scores_str,
        document_text=text,
    )

    try:
        response = llm_fn(prompt)
        # 提取 JSON
        json_match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            subtype = data.get("bid_subtype", "")
            confidence = float(data.get("confidence", 0.0))
            reason = data.get("reason", "")

            # 验证子类型在已知列表中
            if subtype not in _SUBTYPE_KEYWORDS:
                logger.warning(f"SubtypeRouter: LLM returned unknown subtype '{subtype}'")
                return {
                    "bid_subtype": "",
                    "confidence": 0.0,
                    "method": "llm",
                    "candidates": sorted(keyword_scores.items(), key=lambda x: -x[1])[:3],
                    "llm_reason": f"LLM返回了未知子类型: {subtype}",
                }

            ranked = sorted(keyword_scores.items(), key=lambda x: -x[1])
            return {
                "bid_subtype": subtype,
                "confidence": confidence,
                "method": "llm",
                "candidates": ranked[:3],
                "llm_reason": reason,
            }
    except Exception as e:
        logger.warning(f"SubtypeRouter: LLM disambiguation failed: {e}")

    # LLM 调用失败 → 返回低置信度结果
    ranked = sorted(keyword_scores.items(), key=lambda x: -x[1])
    top = ranked[0] if ranked else ("", 0.0)
    return {
        "bid_subtype": top[0],
        "confidence": top[1],
        "method": "fallback",
        "candidates": ranked[:3],
        "llm_reason": "",
    }


# ============================================================
# 补强三: _shared 严格准入与权重控制
# ============================================================

def is_chapter_allowed_in_shared(chapter_key: str) -> bool:
    """检查章节是否允许进入 _shared/ 分区.

    只有格式固定型章节 (投标函/授权委托书等) 才可进入,
    服务方案/技术方案等差异大的章节禁止进入.
    """
    return chapter_key in _SHARED_ALLOWED_CHAPTER_KEYS


def get_retrieval_weights(
    bid_subtype: str,
    kb_root: Path | None = None,
) -> dict[str, float]:
    """根据子类型数据量动态调整 _shared 与子类型分区的权重.

    冷启动 (chunk_count=0):  _shared 0.7 + 最近邻 0.3
    数据不足 (1-4):          自身 1.0 + _shared 0.3
    正常运作 (≥5):           自身 1.0 (停止降级)
    """
    root = kb_root or _DEFAULT_KB_ROOT
    chunk_count = _count_chunks(root / "劳务外包类" / bid_subtype / "chunks")

    if chunk_count >= 5:
        return {"subtype": 1.0, "shared": 0.0, "neighbor": 0.0}
    elif chunk_count >= 1:
        return {"subtype": 1.0, "shared": 0.3, "neighbor": 0.0}
    else:
        # 冷启动: 使用 _shared + 最近邻
        return {"subtype": 0.0, "shared": 0.7, "neighbor": 0.3}


def _count_chunks(chunks_dir: Path) -> int:
    """统计子类型分区下的 chunk 文件数."""
    if not chunks_dir.exists():
        return 0
    return len(list(chunks_dir.glob("*.txt")))


# ============================================================
# 补强四: 新子类型完整冷启动链路
# ============================================================

def find_nearest_subtypes(
    new_subtype: str,
    existing_subtypes: list[str] | None = None,
) -> list[tuple[str, float]]:
    """计算新子类型与所有已有子类型的关键词 Jaccard 相似度.

    Returns:
        [(subtype, jaccard_score), ...] — Top-2 最近邻
    """
    if existing_subtypes is None:
        existing_subtypes = list(_SUBTYPE_KEYWORDS.keys())

    new_keywords = set(_SUBTYPE_KEYWORDS.get(new_subtype, []))
    if not new_keywords:
        return []

    scored: list[tuple[str, float]] = []
    for existing in existing_subtypes:
        if existing == new_subtype:
            continue
        existing_keywords = set(_SUBTYPE_KEYWORDS.get(existing, []))
        if not existing_keywords:
            continue
        # Jaccard 相似度
        intersection = len(new_keywords & existing_keywords)
        union = len(new_keywords | existing_keywords)
        jaccard = intersection / union if union > 0 else 0.0
        scored.append((existing, jaccard))

    scored.sort(key=lambda x: -x[1])
    return scored[:2]


def get_cold_start_info(
    bid_subtype: str,
    kb_root: Path | None = None,
) -> dict:
    """获取子类型的冷启动状态信息 (供 InfoVerificationGate 展示).

    Returns:
        {
            "bid_subtype": str,
            "subtype_status": "normal" | "cold_start" | "data_insufficient",
            "subtype_hint": str,
            "chunk_count": int,
            "nearest_subtypes": [{"name": str, "similarity": float}, ...],
        }
    """
    root = kb_root or _DEFAULT_KB_ROOT
    chunk_count = _count_chunks(root / "劳务外包类" / bid_subtype / "chunks")

    if chunk_count >= 5:
        status = "normal"
        hint = ""
    elif chunk_count >= 1:
        status = "data_insufficient"
        hint = (
            f"该子类型知识库数据不足 ({chunk_count} 条), "
            f"生成内容将参考子类型数据 + 通用模板。建议入库更多真实标书以提升质量。"
        )
    else:
        status = "cold_start"
        hint = (
            "该子类型知识库无数据, 生成内容将参考通用劳务外包模板"
            " + 最相似子类型。建议入库真实标书以提升质量。"
        )

    nearest = find_nearest_subtypes(bid_subtype)
    nearest_info = [
        {"name": name, "similarity": round(sim, 2)}
        for name, sim in nearest
    ]

    return {
        "bid_subtype": bid_subtype,
        "subtype_status": status,
        "subtype_hint": hint,
        "chunk_count": chunk_count,
        "nearest_subtypes": nearest_info,
    }


# ============================================================
# 补强六: manifest.json 版本管理
# ============================================================

def compute_dir_hash(dir_path: Path) -> str:
    """计算目录下所有 .txt/.json/.md 文件的内容哈希.

    用于检测未经入库脚本的直接修改.
    """
    import hashlib

    if not dir_path.exists():
        return ""

    hasher = hashlib.sha256()
    for file in sorted(dir_path.rglob("*")):
        if file.is_file() and file.suffix in (".txt", ".json", ".md"):
            hasher.update(file.read_bytes())
    return hasher.hexdigest()[:12]


def update_manifest(
    subtype_dir: Path,
    action: str = "patch",
    source_info: dict | None = None,
) -> dict:
    """更新子类型分区的 manifest.json.

    Args:
        subtype_dir: 子类型分区目录 (如 knowledge_base/劳务外包类/工业生产线/)
        action: "minor" (新增源标书) | "patch" (修正内容) | "major" (结构变更)
        source_info: 新增源标书信息 {"project_slug": ..., "chunk_count": ..., ...}

    Returns:
        更新后的 manifest dict
    """
    manifest_path = subtype_dir / "manifest.json"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "subtype": subtype_dir.name,
            "version": "0.1.0",
            "last_updated": "",
            "stats": {
                "chunk_count": 0,
                "source_count": 0,
                "pattern_count": 0,
                "prompt_count": 0,
            },
            "sources": [],
            "patterns_hash": "",
            "chunks_hash": "",
        }

    # 版本号更新
    from datetime import date
    today = date.today().isoformat()
    version_parts = [int(x) for x in manifest["version"].split(".")]
    if action == "major":
        version_parts[0] += 1
        version_parts[1] = 0
        version_parts[2] = 0
    elif action == "minor":
        version_parts[1] += 1
        version_parts[2] = 0
    else:  # patch
        version_parts[2] += 1
    manifest["version"] = ".".join(str(x) for x in version_parts)
    manifest["last_updated"] = today

    # 更新统计
    chunks_dir = subtype_dir / "chunks"
    patterns_dir = subtype_dir / "patterns"
    prompts_dir = subtype_dir / "prompts"
    manifest["stats"]["chunk_count"] = _count_chunks(chunks_dir) if chunks_dir.exists() else 0
    manifest["stats"]["pattern_count"] = (
        len(list(patterns_dir.glob("*.json"))) if patterns_dir.exists() else 0
    )
    manifest["stats"]["prompt_count"] = (
        len(list(prompts_dir.glob("*.md"))) if prompts_dir.exists() else 0
    )

    # 哈希校验
    if chunks_dir.exists():
        manifest["chunks_hash"] = compute_dir_hash(chunks_dir)
    if patterns_dir.exists():
        manifest["patterns_hash"] = compute_dir_hash(patterns_dir)

    # 添加来源记录
    if source_info:
        manifest["sources"].append(source_info)
        manifest["stats"]["source_count"] = len(manifest["sources"])

    # 写入
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        f"SubtypeRouter: manifest updated for '{subtype_dir.name}' "
        f"v{manifest['version']} ({action})"
    )
    return manifest



