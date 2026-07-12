#!/usr/bin/env python3
"""Build the knowledge base — generate complete bid templates and chapter essays.

Phase 0 of the optimization plan.  Uses the existing PDF bid documents as source
material and an LLM to generate:

  1. Full 8-chapter bid templates   → knowledge_base/{type}/模板/
  2. Per-chapter model essays       → knowledge_base/{type}/范文/
  3. FAISS index metadata           → knowledge_base/{type}/metadata.json

All sensitive / real-world data (IDs, financials, certificates, etc.) is replaced
with highlighted 【待填写：...】placeholders so the user knows exactly what to
fill in before submitting.
"""

import json
import logging
import sys
from pathlib import Path

# ── Add project root to path ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm.providers import create_llm, test_connection
from core.nodes.doc_parser import _load_pdf
from core.config_persistence import load_config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# ── Config ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# knowledge_base sits at the workspace root, one level above bid-agent/
KB_ROOT = PROJECT_ROOT.parent / "knowledge_base"

# Mapping: bid type → source PDFs available
BID_TYPE_SOURCES: dict[str, list[str]] = {
    "服务类": ["复旦大学物业_招标文件.pdf"],
    "货物类": ["长春应化所_招标文件.pdf"],
    "工程类": ["浙大紫金港景观_招标文件.pdf"],
    "运维类": ["江苏税务局运维_招标文件.pdf"],
    "集成类": ["中央民大数智门户_招标文件.pdf"],
    "劳务外包类": ["厦门税务局食堂外包_招标文件.pdf"],
}

# 8 standard chapters for government procurement bids
SECTIONS = [
    {"name": "第一章 投标函", "key": "ch1_letter"},
    {"name": "第二章 法定代表人授权委托书", "key": "ch2_authorization"},
    {"name": "第三章 服务方案", "key": "ch3_service"},
    {"name": "第四章 技术方案", "key": "ch4_technical"},
    {"name": "第五章 人员配置", "key": "ch5_staffing"},
    {"name": "第六章 公司资质与业绩", "key": "ch6_qualifications"},
    {"name": "第七章 项目实施计划", "key": "ch7_schedule"},
    {"name": "第八章 售后服务承诺", "key": "ch8_after_sales"},
]

# ── Placeholder convention ─────────────────────────────────────────────
# Format: 【待填写：具体说明】
# Examples:
#   【待填写：营业执照上的公司全称，如"XX科技有限公司"】
#   【待填写：法定代表人姓名】
#   【待填写：项目团队成员的身份证号】
#   【待填写：近三年审计报告中的营业收入数据，需与财报一致】
#   【待填写：ISO9001质量管理体系认证证书编号及有效期】
#   【待填写：XX项目合同金额及签约日期】

PLACEHOLDER_RULES = """
对于以下类型的真实数据，必须使用【待填写：具体说明】格式留白，不得编造任何数据：
1. 个人身份证号、手机号、家庭住址
2. 企业营业执照号、统一社会信用代码
3. 财务数据（营业收入、利润、资产总额、纳税额）
4. 认证证书编号（ISO系列、行业许可等）
5. 合同金额、项目金额
6. 具体日期（合同签订日期、证书有效期等）
7. 人名（法定代表人、项目成员、客户联系人等）
8. 银行账号、保证金金额
9. 政府部门名称（具体的区/县/市级别）

对于可公开发布的信息可以正常撰写：
- 行业通用的技术方案描述
- 标准化的服务流程
- 通用的管理方法论
- 法律法规引用
"""

# ── Prompts ─────────────────────────────────────────────────────────────

TEMPLATE_SYSTEM_PROMPT = f"""你是一位资深政府采购招投标专家，有15年标书撰写经验。

请根据以下招标文件的要求，生成一份完整的8章投标文件模板。

{PLACEHOLDER_RULES}

输出格式：请严格按照以下8个章节结构输出，每章之间用"=========="分隔。
每章先输出章节标题（如"第一章 投标函"），然后输出章节内容。

第一章 投标函
==========
第二章 法定代表人授权委托书
==========
第三章 服务方案
==========
第四章 技术方案
==========
第五章 人员配置
==========
第六章 公司资质与业绩
==========
第七章 项目实施计划
==========
第八章 售后服务承诺
==========

请确保：
1. 每个章节都针对招标文件的具体要求逐条回应
2. 语言专业严谨，格式规范
3. 敏感数据全部用【待填写：XXX】留白
4. 突出投标人的竞争优势和专业能力"""

ESSAY_SYSTEM_PROMPT = f"""你是一位资深政府采购招投标专家。请为标书的这一章节撰写一篇高分范文。

{PLACEHOLDER_RULES}

请撰写一篇可以直接用于投标的范文，要求：
1. 语言专业严谨，论证充分
2. 结构清晰，层次分明
3. 每个论点有具体支撑
4. 敏感数据全部用【待填写：XXX】留白
5. 标注出本章的得分要点和撰写策略"""

# ── Helpers ─────────────────────────────────────────────────────────────


def parse_source_pdf(bid_type: str) -> str:
    """Parse all source PDFs for a bid type and return concatenated text."""
    texts: list[str] = []
    source_dir = KB_ROOT / bid_type / "招标文件"

    for filename in BID_TYPE_SOURCES.get(bid_type, []):
        filepath = source_dir / filename
        if not filepath.exists():
            logger.warning("Source PDF not found: %s", filepath)
            continue
        try:
            text = _load_pdf(str(filepath))
            texts.append(text)
            logger.info("Parsed %s: %d chars", filename, len(text))
        except Exception as e:
            logger.error("Failed to parse %s: %s", filename, e)

    combined = "\n\n".join(texts)
    if not combined.strip():
        raise RuntimeError(f"No usable text extracted from {bid_type} PDFs")
    return combined


def extract_requirements(source_text: str, llm) -> dict:
    """Extract structured requirements from source PDF text."""
    prompt = """请从以下招标文件中提取关键信息，以JSON格式返回：

{
  "project_name": "项目名称",
  "bid_number": "招标编号",
  "procurement_agency": "采购单位",
  "scoring_criteria": [
    {"item": "评分项名称", "max_score": 分数, "description": "评分标准描述"}
  ],
  "qualification_requirements": ["资质要求1", "资质要求2"],
  "technical_requirements": ["技术要求1", "技术要求2"],
  "key_clauses": ["关键条款1", "关键条款2"],
  "bid_type_summary": "一句话概括本项目类型和主要采购内容"
}

招标文件内容：
""" + source_text[:15000]

    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content if hasattr(response, "content") else str(response)

    # Extract JSON
    import re
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, using raw response")
    return {"raw_extraction": content}


def generate_template(bid_type: str, source_text: str, llm) -> str:
    """Generate a complete 8-chapter bid template."""
    prompt = TEMPLATE_SYSTEM_PROMPT + "\n\n"
    prompt += f"标书类型：{bid_type}\n\n"
    prompt += "招标文件关键内容（提取自真实招标文件）：\n"
    # Include the first 12000 chars for context
    prompt += source_text[:12000]
    prompt += "\n\n请严格按照格式要求生成完整的投标文件模板。"

    from langchain_core.messages import HumanMessage
    logger.info("Generating full template for %s ...", bid_type)
    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content if hasattr(response, "content") else str(response)


def generate_chapter_essay(
    bid_type: str, chapter: dict, source_text: str, llm
) -> str:
    """Generate a model essay for a single chapter."""
    prompt = ESSAY_SYSTEM_PROMPT + "\n\n"
    prompt += f"标书类型：{bid_type}\n"
    prompt += f"章节名称：{chapter['name']}\n\n"
    prompt += "招标文件相关内容（供参考）：\n"
    prompt += source_text[:8000]
    prompt += f"\n\n请为「{chapter['name']}」撰写一篇高分范文。"

    from langchain_core.messages import HumanMessage
    logger.info("  Generating essay for %s ...", chapter['name'])
    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content if hasattr(response, "content") else str(response)


# ── Main ────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="构建标书知识库模板和范文")
    parser.add_argument(
        "--type", type=str, default=None,
        help="只处理指定标书类型（如：服务类、货物类），不指定则处理全部"
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="跳过已有模板和范文的标书类型"
    )
    parser.add_argument(
        "--essays-only", action="store_true",
        help="只生成范文，不生成完整模板（用于增量补充）"
    )
    args = parser.parse_args()

    # Load API config
    config = load_config()
    provider = config.get("provider", "deepseek")
    model = config.get("model", "deepseek-chat")  # N03 fix
    api_keys = config.get("api_keys", {})
    api_key = api_keys.get(provider, "")

    if not api_key:
        logger.error(
            "No API key configured. Please configure in the GUI first."
        )
        sys.exit(1)

    # Validate connection
    ok, msg = test_connection(provider, api_key, model)
    if not ok:
        logger.error("API connection failed: %s", msg)
        sys.exit(1)
    logger.info("API connection OK: %s/%s", provider, model)

    # Create LLM
    llm = create_llm(provider, api_key=api_key, model=model, temperature=0.3)

    # Process each bid type
    types_to_process = [args.type] if args.type else list(BID_TYPE_SOURCES.keys())

    for bid_type in types_to_process:
        if bid_type not in BID_TYPE_SOURCES:
            logger.error("Unknown bid type: %s. Available: %s", bid_type, list(BID_TYPE_SOURCES.keys()))
            continue

        # Skip if already built
        template_dir = KB_ROOT / bid_type / "模板"
        essay_dir = KB_ROOT / bid_type / "范文"
        if args.skip_existing and template_dir.exists() and essay_dir.exists():
            existing_templates = list(template_dir.glob("*.txt"))
            existing_essays = list(essay_dir.glob("*.txt"))
            if len(existing_templates) >= 1 and len(existing_essays) >= 8:
                logger.info("Skipping %s — already has %d templates + %d essays", bid_type, len(existing_templates), len(existing_essays))
                continue
        logger.info("=" * 60)
        logger.info("Processing: %s", bid_type)

        try:
            # Step 1: Parse source PDF
            source_text = parse_source_pdf(bid_type)

            # Step 2: Extract requirements (for metadata)
            requirements = extract_requirements(source_text, llm)
            logger.info(
                "Extracted requirements: %s",
                json.dumps(requirements, ensure_ascii=False)[:200],
            )

            # Step 3: Generate full template (skip if --essays-only)
            if not args.essays_only:
                template = generate_template(bid_type, source_text, llm)

                # Step 4: Save full template
                template_dir = KB_ROOT / bid_type / "模板"
                template_dir.mkdir(parents=True, exist_ok=True)
                template_path = template_dir / f"{bid_type}_完整标书模板.txt"
                template_path.write_text(template, encoding="utf-8")
                logger.info("Saved template: %s (%d chars)", template_path, len(template))

            # Step 5: Generate chapter essays
            essay_dir = KB_ROOT / bid_type / "范文"
            essay_dir.mkdir(parents=True, exist_ok=True)

            for chapter in SECTIONS:
                essay = generate_chapter_essay(bid_type, chapter, source_text, llm)
                chapter_file = essay_dir / f"{chapter['key']}_{chapter['name']}.txt"
                chapter_file.write_text(essay, encoding="utf-8")
                logger.info("  Saved essay: %s (%d chars)", chapter_file.name, len(essay))

            # Step 6: Save metadata
            metadata = {
                "bid_type": bid_type,
                "generated_at": None,  # filled below
                "source_pdfs": BID_TYPE_SOURCES.get(bid_type, []),
                "requirements": requirements,
                "chapter_keys": [s["key"] for s in SECTIONS],
            }
            from datetime import datetime, timezone
            metadata["generated_at"] = datetime.now(timezone.utc).isoformat()

            meta_path = KB_ROOT / bid_type / "metadata.json"
            meta_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Saved metadata: %s", meta_path)

        except Exception as e:
            logger.error("Failed for %s: %s", bid_type, e, exc_info=True)
            continue

    logger.info("=" * 60)
    logger.info("Knowledge base build complete!")
    logger.info("Output: %s", KB_ROOT)


if __name__ == "__main__":
    main()
