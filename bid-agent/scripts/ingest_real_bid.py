#!/usr/bin/env python3.10
"""入库脚本：将真实成品标书PDF解析→按章节切分→保存到知识库范文目录。

用法:
    # 广铁标书（9章物资采购格式）
    python3.10 scripts/ingest_real_bid.py --pdf "../2026年广州铁道车辆有限公司劳务管理服务采购（技术标）v2.pdf" --label "广铁真实标书"

    # 广晟标书（5章政府采购格式 / HRO）
    python3.10 scripts/ingest_real_bid.py --pdf "../投标文件（已盖章）.pdf" --label "广晟真实标书"

文件名约定（检索器靠 section_key in filename 匹配）:
    sec1_bid_letter_广铁真实标书_001.txt
    sec7_service_plan_广晟真实标书_001.txt
    ...
"""

import argparse
import re
import sys
from pathlib import Path

# ── 配置 ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_ROOT = PROJECT_ROOT.parent / "knowledge_base"
ESSAY_DIR = KB_ROOT / "劳务外包类" / "范文"

# 章节标题关键词 → section_key 映射（兼容多种标书格式）
# 匹配时按此顺序逐条检查标题行
SECTION_KEY_MAP = {
    # 资格证明类
    "投标函": "sec1_bid_letter",
    "法定代表人": "sec2_legal_rep",
    "授权委托书": "sec3_authorization",
    "投标保证金": "sec4_deposit",
    # 偏差/响应表类
    "偏差表": "sec5_deviation",
    "商务及合同条款响应": "sec5_deviation",
    "用户需求书响应": "sec5_deviation",
    # 报价类
    "分项报价": "sec5b_price_table",
    "开标一览表": "sec5b_price_table",
    "报价表": "sec5b_price_table",
    # 资格审查/商务类
    "资格审查": "sec6_qualification",
    "综合概况": "sec6_qualification",
    "企业概况": "sec6_qualification",
    "审计报告": "sec6_qualification",
    "项目业绩": "sec6_qualification",
    "诉讼及仲裁": "sec6_qualification",
    "信誉": "sec6_qualification",
    # 服务方案类（技术标核心）
    "服务方案": "sec7_service_plan",
    "拟派本项目负责人": "sec7_service_plan",
    "拟派": "sec7_service_plan",
    "商业补充医疗保险": "sec7_service_plan",
    "雇主责任险": "sec7_service_plan",
    "服务范围": "sec7_service_plan",
    "服务承诺": "sec7_service_plan",
    # 应急保障类
    "应急保障": "sec8_emergency",
}

# 跳过的章节关键词（不计入知识库）
SKIP_KEYWORDS = {"自查表", "廉洁自律", "目录", "承诺书"}

MAX_CHUNK_SIZE = 1000
MIN_CHUNK_SIZE = 100


def parse_pdf(pdf_path: str) -> str:
    """用 PyMuPDF 解析 PDF，返回全文文本。"""
    import fitz
    doc = fitz.open(pdf_path)
    text_parts = []
    for page in doc:
        page_text = page.get_text()
        if page_text and page_text.strip():
            text_parts.append(page_text)
    doc.close()
    return "\n".join(text_parts)


def clean_page_numbers(content: str) -> str:
    """去掉PDF页码（独立成行的纯数字）和多余空行。"""
    lines = content.split("\n")
    cleaned = [l for l in lines if not re.match(r"^\d{1,3}$", l.strip())]
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    return result.strip()


def _match_section_key(title_lines: list[str]) -> str | None:
    """检查前几行文本，匹配 section_key。

    支持两种标题格式:
    - 9章格式: "一、投标函" （标题在同一行）
    - 5章格式: "2.1\\n投标函" （编号独立成行，标题在下一行）
    """
    # 检查前 2 行（处理编号独立成行的情况）
    check_text = " ".join(title_lines[:2]).strip()

    # 先检查是否应跳过
    for skip_kw in SKIP_KEYWORDS:
        if skip_kw in check_text:
            return None

    # 逐条匹配关键词
    for keyword, key in SECTION_KEY_MAP.items():
        if keyword in check_text:
            return key

    return None


def split_by_chapter(text: str) -> list[dict]:
    """按章节标题切分全文。

    支持两种切分模式:
    1. 顶层章节: "一、xxx" "二、xxx" ...
    2. 子章节: "2.1\\n投标函" "4.2.\\n服务方案" ...

    跳过目录页（含连续省略号+页码）。
    同一 section_key 允许多个块（不同子章节可映射到同一 key），
    仅当内容开头80字相同时才去重（处理目录页/正文重复）。

    返回: [{"title": "投标函", "key": "sec1_bid_letter", "content": "..."}]
    """
    # 切分模式：顶层章节（一、二、...）或子章节编号（2.1 / 4.2. 等独立成行）
    pattern = r"(?=^(?:[一二三四五六七八九十]+、|\d+\.\d+\.?\s*$))"
    raw_chunks = re.split(pattern, text, flags=re.MULTILINE)

    chapters: list[dict] = []

    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk or len(chunk) < MIN_CHUNK_SIZE:
            continue

        # 跳过目录页（含大量 "....." 页码引用）
        if re.search(r"\.{5,}", chunk[:200]):
            continue

        # 取前几行用于匹配
        lines = chunk.split("\n")
        title_lines = [l.strip() for l in lines[:3] if l.strip()]
        if not title_lines:
            continue

        section_key = _match_section_key(title_lines)
        if not section_key:
            # 尝试更宽松的匹配：检查前 5 行
            broad_text = " ".join(lines[:5])
            for skip_kw in SKIP_KEYWORDS:
                if skip_kw in broad_text:
                    section_key = None
                    break
            if not section_key:
                print(f"  ⚠️ 跳过（未匹配）: {title_lines[0][:40]}...")
                continue

        # 提取标题（用于日志显示）
        title = title_lines[0] if len(title_lines[0]) > 2 else (
            title_lines[1] if len(title_lines) > 1 else title_lines[0]
        )

        # 去重：仅当内容开头80字相同时才视为重复（处理目录页/正文重复）
        # 不同子章节即使映射到同一 section_key 也全部保留
        chunk_prefix = chunk[:80]
        is_duplicate = False
        for existing in chapters:
            if existing["key"] == section_key and existing["content"][:80] == chunk_prefix:
                # 真正的重复，保留更长的
                if len(chunk) > len(existing["content"]):
                    existing["content"] = chunk
                    existing["title"] = title
                    print(f"  🔄 去重更新 {section_key}（同内容更长版本）| {len(chunk)}字")
                is_duplicate = True
                break

        if is_duplicate:
            continue

        chapters.append({"title": title, "key": section_key, "content": chunk})
        print(f"  ✅ {section_key} | {title[:40]} | {len(chunk)}字")

    return chapters


def split_large_chunk(content: str, max_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """将过大的章节按段落切分为多个块，清理页码。"""
    content = clean_page_numbers(content)
    if len(content) <= max_size:
        return [content] if len(content) >= 50 else []

    paragraphs = re.split(r"\n", content)
    paragraphs = [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 20]

    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 1 > max_size and current:
            chunks.append(current)
            current = p
        else:
            current = current + "\n" + p if current else p

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c) >= 50]


def save_chunks(chapters: list[dict], source_label: str, bid_subtype: str = "") -> int:
    """保存章节块为 .txt 文件。先清理同标签旧文件。

    同一 section_key 的多个章节使用全局递增编号，避免覆盖。
    当 bid_subtype 非空时，保存到子类型分区 chunks/ 目录；
    否则保存到范文目录。
    """
    # 确定保存目录
    if bid_subtype:
        target_dir = KB_ROOT / "劳务外包类" / bid_subtype / "chunks"
        shared_dir = KB_ROOT / "劳务外包类" / "_shared" / "chapters"
    else:
        target_dir = ESSAY_DIR
        shared_dir = None

    target_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    old_files = list(target_dir.glob(f"*_{source_label}_*.txt"))
    for f in old_files:
        f.unlink()
    if old_files:
        print(f"  🗑️ 清理旧文件: {len(old_files)} 个")

    # _shared 准入白名单（只有格式固定型章节才可进入 _shared/）
    shared_allowed_keys = {
        "sec1_bid_letter", "sec2_legal_rep", "sec3_authorization",
        "sec4_deposit", "sec5_deviation",
    }

    saved_count = 0
    # 每个 section_key 的文件序号计数器（跨章节递增）
    key_counters: dict[str, int] = {}

    for ch in chapters:
        sub_chunks = split_large_chunk(ch["content"])
        for sub in sub_chunks:
            key = ch["key"]
            key_counters[key] = key_counters.get(key, 0) + 1
            idx = key_counters[key]
            filename = f"{key}_{source_label}_{idx:03d}.txt"
            filepath = target_dir / filename
            filepath.write_text(sub, encoding="utf-8")
            saved_count += 1
            print(f"  💾 {filename} ({len(sub)}字)")

            # 95+优化 补强三: 格式固定型章节同步到 _shared
            if bid_subtype and shared_dir and key in shared_allowed_keys:
                shared_dir.mkdir(parents=True, exist_ok=True)
                shared_filepath = shared_dir / filename
                shared_filepath.write_text(sub, encoding="utf-8")

    return saved_count


def verify_retrieval(source_label: str, bid_subtype: str = ""):
    """验证检索器能检索到新入库的内容。"""
    print("\n🔍 验证检索...")
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from core.retrieval.reference_retriever import ReferenceRetriever
        retriever = ReferenceRetriever()

        # 确定搜索目录: 子类型分区或范文目录
        if bid_subtype:
            search_dir = KB_ROOT / "劳务外包类" / bid_subtype / "chunks"
        else:
            search_dir = ESSAY_DIR

        test_keys = [
            "sec7_service_plan",
            "sec8_emergency",
            "sec1_bid_letter",
            "sec3_authorization",
            "sec6_qualification",
            "sec5b_price_table",
        ]

        for key in test_keys:
            snippet = retriever.retrieve_for_section(
                key, "劳务外包类", bid_subtype=bid_subtype
            )
            if snippet:
                # 检查是否包含本次入库的内容
                files = list(search_dir.glob(f"{key}_{source_label}_*"))
                if files:
                    print(f"  ✅ {key} | {source_label} 文件数: {len(files)} | 检索到 {len(snippet)} 字符")
                else:
                    print(f"  ⚠️ {key} | {source_label} 无文件 | 检索到 {len(snippet)} 字符（来自其他来源）")
            else:
                print(f"  ❌ {key} 未检索到内容")

        # 显示服务方案预览
        snippet = retriever.retrieve_for_section(
            "sec7_service_plan", "劳务外包类", bid_subtype=bid_subtype
        )
        if snippet:
            print("\n  📋 sec7_service_plan 预览:")
            print(f"  {snippet[:200]}...")

    except Exception as e:
        print(f"  ⚠️ 检索验证失败: {e}")
        print(f"  请手动验证: ls {search_dir}/*{source_label}*")


def main():
    parser = argparse.ArgumentParser(description="入库真实成品标书PDF到知识库")
    parser.add_argument(
        "--pdf", required=True, help="PDF文件路径（相对于项目根或绝对路径）"
    )
    parser.add_argument(
        "--label", required=True, help="来源标签（如：广铁真实标书、广晟真实标书）"
    )
    parser.add_argument(
        "--subtype", default="", help="劳务外包子类型（如：HRO、食堂餐饮、工业生产线）—— 95+优化"
    )
    args = parser.parse_args()

    # 解析PDF路径
    pdf_path = Path(args.pdf)
    if not pdf_path.is_absolute():
        pdf_path = PROJECT_ROOT.parent / pdf_path

    if not pdf_path.exists():
        print(f"❌ PDF文件不存在: {pdf_path}")
        sys.exit(1)

    print(f"📄 解析PDF: {pdf_path.name}")
    text = parse_pdf(str(pdf_path))
    print(f"   总字符数: {len(text)}")

    print("\n✂️ 按章节切分:")
    chapters = split_by_chapter(text)
    print(f"   共 {len(chapters)} 个正文章节")

    if not chapters:
        print("❌ 未匹配到任何章节，请检查PDF内容或SECTION_KEY_MAP")
        sys.exit(1)

    print(f"\n💾 保存到: {'子类型分区' if args.subtype else '范文目录'}")
    count = save_chunks(chapters, args.label, bid_subtype=args.subtype)
    print(f"\n🎉 入库完成！共保存 {count} 个文件")

    # 95+优化 补强五: 表格提取入库
    if args.subtype:
        print("\n📊 表格结构化提取:")
        table_count = extract_and_save_tables(pdf_path, args.subtype, args.label)
        print(f"   共保存 {table_count} 个表格 JSON")

        # 95+优化 补强六: manifest 更新
        print("\n📋 版本管理:")
        update_subtype_manifest(args.subtype, args.label, count, table_count)

    verify_retrieval(args.label, bid_subtype=args.subtype)


def extract_and_save_tables(pdf_path: Path, bid_subtype: str, source_label: str) -> int:
    """从 PDF 中提取表格，保存为结构化 JSON 到子类型 patterns 目录。

    95+优化 补强五: 将评分表/资格审查表等结构化表格提取为 JSON 模式,
    供 ReqExtractor 和 SectionGenerator 使用。

    Returns:
        保存的表格 JSON 文件数量
    """
    import json

    import fitz

    patterns_dir = KB_ROOT / "劳务外包类" / bid_subtype / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    saved_count = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        tables = page.find_tables()

        for table_idx, table in enumerate(tables):
            rows = table.extract()
            if not rows or len(rows) < 2:  # 跳过空表或只有表头
                continue

            # 尝试推断表格类型
            header_text = " ".join(str(c) for c in rows[0] if c)
            table_type = "unknown"
            if any(kw in header_text for kw in ["评分", "评审", "打分", "分值"]):
                table_type = "scoring"
            elif any(kw in header_text for kw in ["资格", "资质", "审查", "准入"]):
                table_type = "qualification"
            elif any(kw in header_text for kw in ["报价", "价格", "金额", "单价"]):
                table_type = "pricing"
            elif any(kw in header_text for kw in ["人员", "staffing", "岗位", "人数"]):
                table_type = "staffing"

            # 跳过无意义的小表格
            if table_type == "unknown" and len(rows) < 3:
                continue

            table_data = {
                "source": source_label,
                "subtype": bid_subtype,
                "page": page_num + 1,
                "table_index": table_idx,
                "type": table_type,
                "header": [str(c) if c else "" for c in rows[0]],
                "rows": [[str(c) if c else "" for c in row] for row in rows[1:]],
                "row_count": len(rows) - 1,
            }

            filename = f"{table_type}_p{page_num + 1}_{table_idx}_{source_label}.json"
            filepath = patterns_dir / filename
            filepath.write_text(
                json.dumps(table_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            saved_count += 1
            print(f"  📊 {filename} ({len(rows) - 1}行, 类型={table_type})")

    doc.close()
    return saved_count


def update_subtype_manifest(
    bid_subtype: str, source_label: str, chunk_count: int, table_count: int
) -> None:
    """更新子类型分区的 manifest.json 版本管理文件。

    95+优化 补强六: 使用 SubtypeRouter.update_manifest 进行版本管理。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.retrieval.subtype_router import update_manifest

    subtype_dir = KB_ROOT / "劳务外包类" / bid_subtype
    source_info = {
        "project_slug": source_label,
        "chunk_count": chunk_count,
        "table_count": table_count,
        "ingested_at": str(__import__("datetime").date.today()),
    }
    manifest = update_manifest(subtype_dir, action="minor", source_info=source_info)
    print(f"  ✅ manifest.json 更新完成: v{manifest['version']}")
    print(f"   统计: chunks={manifest['stats']['chunk_count']}, "
          f"patterns={manifest['stats']['pattern_count']}, "
          f"sources={manifest['stats']['source_count']}")


if __name__ == "__main__":
    main()
