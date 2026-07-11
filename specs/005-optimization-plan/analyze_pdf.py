"""解析真实标书 PDF，提取结构和内容特征用于优化 prompt 和范文。"""
import fitz
import json
import re
from pathlib import Path

PDF_PATH = "/Users/alanchris/Desktop/标书agent01/2026年广州铁道车辆有限公司劳务管理服务采购（技术标）v2.pdf"
OUT_DIR = Path("/Users/alanchris/Desktop/标书agent02/specs/005-optimization-plan/analysis")

doc = fitz.open(PDF_PATH)
total_pages = len(doc)
print(f"总页数: {total_pages}")

# 提取全部文本
full_text_parts = []
for i, page in enumerate(doc):
    text = page.get_text()
    full_text_parts.append(text)
full_text = "\n".join(full_text_parts)
doc.close()

print(f"总字符数: {len(full_text)}")

# 保存全文到临时文件
tmp_path = OUT_DIR / "_full_text.txt"
OUT_DIR.mkdir(parents=True, exist_ok=True)
tmp_path.write_text(full_text, encoding="utf-8")
print(f"全文已保存到: {tmp_path}")

# 分析章节结构：识别「第X章」「一、」「二、」等标题
chapter_pattern = re.compile(r"第[一二三四五六七八九十百\d]+章[^\n]*", re.MULTILINE)
section_pattern = re.compile(r"^[一二三四五六七八九十]+[、．.][^\n]{2,40}", re.MULTILINE)
subsection_pattern = re.compile(r"^[(（][一二三四五六七八九十\d]+[)）][^\n]{2,40}", re.MULTILINE)

chapters = chapter_pattern.findall(full_text)
sections = section_pattern.findall(full_text)
subsections = subsection_pattern.findall(full_text)

print(f"\n=== 章节标题（{len(chapters)} 个）===")
for c in chapters[:30]:
    print(f"  {c.strip()}")

print(f"\n=== 一级小节（{len(sections)} 个，前20）===")
for s in sections[:20]:
    print(f"  {s.strip()}")

print(f"\n=== 二级小节（{len(subsections)} 个，前15）===")
for s in subsections[:15]:
    print(f"  {s.strip()}")

# 分析数据特征：数字、百分比、表格
numbers = re.findall(r"\d+\.?\d*[%％人万元天个月年个项类]", full_text)
print(f"\n=== 数据点统计 ===")
print(f"含单位的数字: {len(numbers)} 个")
print(f"示例: {numbers[:15]}")

# 识别关键内容特征
features = {
    "total_pages": total_pages,
    "total_chars": len(full_text),
    "chapter_count": len(chapters),
    "section_count": len(sections),
    "subsection_count": len(subsections),
    "number_with_unit_count": len(numbers),
    "avg_chars_per_page": len(full_text) // total_pages if total_pages else 0,
}

# 识别文档类型特征
type_features = {
    "has_table_of_contents": bool(re.search(r"目\s*录", full_text[:3000])),
    "has_company_intro": bool(re.search(r"公司简介|公司概况|企业简介", full_text)),
    "has_service_plan": bool(re.search(r"服务方案|服务内容|服务承诺", full_text)),
    "has_tech_plan": bool(re.search(r"技术方案|技术路线|技术措施", full_text)),
    "has_staffing": bool(re.search(r"人员配置|人员配备|组织机构|组织架构", full_text)),
    "has_qualifications": bool(re.search(r"资质|证书|许可证", full_text)),
    "has_performance": bool(re.search(r"业绩|项目经验|案例", full_text)),
    "has_schedule": bool(re.search(r"实施计划|进度计划|时间安排", full_text)),
    "has_after_sales": bool(re.search(r"售后|质保|维护|运维", full_text)),
    "has_risk_management": bool(re.search(r"风险|应急|预案", full_text)),
    "has_quality_assurance": bool(re.search(r"质量保证|质量控制|质量管理", full_text)),
    "has_pricing": bool(re.search(r"报价|预算|费用|成本", full_text)),
    "has_staff_management": bool(re.search(r"劳务|用工|派遣|外包", full_text)),  # 劳务管理服务特有
}

print(f"\n=== 文档特征 ===")
for k, v in {**features, **type_features}.items():
    print(f"  {k}: {v}")

# 保存特征到 JSON
(features_path := OUT_DIR / "doc_features.json").write_text(
    json.dumps({**features, **type_features}, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"\n特征已保存: {features_path}")
