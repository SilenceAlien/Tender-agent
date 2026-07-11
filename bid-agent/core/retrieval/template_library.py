"""Template library builder — creates FAISS indices for 7 bid types.

Contract (T027):
    - 7 types: 服务, 货物, 软件, 工程, 集成, 运维, 劳务外包
    - ≥3 templates per type
    - Each template has: id, name, type, section_keys, vector
"""

import logging
from pathlib import Path

import numpy as np

from core.retrieval.embeddings import MockEmbedder
from core.retrieval.faiss_index import VectorIndexManager

logger = logging.getLogger(__name__)

# ── Template library ───────────────────────────────────────────────────

TEMPLATES = {
    "服务": [
        {
            "id": "tpl_service_v1",
            "name": "物业服务标书模板（标准版）",
            "type": "服务",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch8_after_sales"],
            "description": "通用物业服务标准模板，适用于政府物业采购",
        },
        {
            "id": "tpl_service_v2",
            "name": "劳务管理服务标书模板",
            "type": "服务",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch7_schedule"],
            "description": "劳务派遣/管理服务专用模板，强调人员管理能力",
        },
        {
            "id": "tpl_service_v3",
            "name": "IT运维服务标书模板",
            "type": "服务",
            "sections": ["ch1_letter", "ch4_technical", "ch5_staffing", "ch8_after_sales"],
            "description": "IT运维服务模板，强调技术能力和SLA",
        },
        {
            "id": "tpl_service_v4",
            "name": "综合后勤服务标书模板",
            "type": "服务",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing"],
            "description": "食堂/保洁/安保等综合后勤服务模板",
        },
    ],
    "货物": [
        {
            "id": "tpl_goods_v1",
            "name": "设备采购标书模板（标准版）",
            "type": "货物",
            "sections": ["ch1_letter", "ch4_technical", "ch6_qualifications"],
            "description": "通用设备采购模板",
        },
        {
            "id": "tpl_goods_v2",
            "name": "物资供应标书模板",
            "type": "货物",
            "sections": ["ch1_letter", "ch3_service", "ch4_technical"],
            "description": "大批量物资供应模板",
        },
        {
            "id": "tpl_goods_v3",
            "name": "医疗器械采购标书模板",
            "type": "货物",
            "sections": ["ch1_letter", "ch4_technical", "ch6_qualifications", "ch8_after_sales"],
            "description": "医疗器械类专用模板，强调资质和技术参数",
        },
    ],
    "软件": [
        {
            "id": "tpl_software_v1",
            "name": "软件开发标书模板",
            "type": "软件",
            "sections": ["ch1_letter", "ch4_technical", "ch5_staffing", "ch7_schedule"],
            "description": "软件定制开发模板，强调开发流程和团队",
        },
        {
            "id": "tpl_software_v2",
            "name": "SaaS平台服务标书模板",
            "type": "软件",
            "sections": ["ch1_letter", "ch3_service", "ch4_technical", "ch8_after_sales"],
            "description": "SaaS平台/云服务模板",
        },
        {
            "id": "tpl_software_v3",
            "name": "信息化建设标书模板",
            "type": "软件",
            "sections": ["ch1_letter", "ch4_technical", "ch6_qualifications", "ch7_schedule"],
            "description": "大型信息化建设项目模板",
        },
    ],
    "工程": [
        {
            "id": "tpl_engineering_v1",
            "name": "建筑工程标书模板",
            "type": "工程",
            "sections": ["ch1_letter", "ch4_technical", "ch5_staffing", "ch6_qualifications", "ch7_schedule"],
            "description": "房屋建筑/市政工程类模板",
        },
        {
            "id": "tpl_engineering_v2",
            "name": "装修工程标书模板",
            "type": "工程",
            "sections": ["ch1_letter", "ch4_technical", "ch7_schedule"],
            "description": "室内装修/装修改造工程模板",
        },
        {
            "id": "tpl_engineering_v3",
            "name": "机电安装工程标书模板",
            "type": "工程",
            "sections": ["ch1_letter", "ch4_technical", "ch5_staffing", "ch6_qualifications"],
            "description": "机电设备安装工程模板",
        },
    ],
    "集成": [
        {
            "id": "tpl_integration_v1",
            "name": "系统集成标书模板",
            "type": "集成",
            "sections": ["ch1_letter", "ch3_service", "ch4_technical", "ch7_schedule"],
            "description": "软硬件系统集成模板",
        },
        {
            "id": "tpl_integration_v2",
            "name": "弱电智能化标书模板",
            "type": "集成",
            "sections": ["ch1_letter", "ch4_technical", "ch5_staffing", "ch7_schedule"],
            "description": "弱电智能化系统集成模板",
        },
        {
            "id": "tpl_integration_v3",
            "name": "安防系统集成标书模板",
            "type": "集成",
            "sections": ["ch1_letter", "ch4_technical", "ch6_qualifications"],
            "description": "安防监控/门禁/报警系统集成模板",
        },
    ],
    "运维": [
        {
            "id": "tpl_operations_v1",
            "name": "IT运维标书模板",
            "type": "运维",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch8_after_sales"],
            "description": "IT系统运维/网络运维模板",
        },
        {
            "id": "tpl_operations_v2",
            "name": "设备维保标书模板",
            "type": "运维",
            "sections": ["ch1_letter", "ch3_service", "ch4_technical", "ch8_after_sales"],
            "description": "设备维护保养模板",
        },
        {
            "id": "tpl_operations_v3",
            "name": "物业运维标书模板",
            "type": "运维",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch7_schedule"],
            "description": "物业设施设备运维模板",
        },
    ],
    "劳务外包": [
        {
            "id": "tpl_labor_v1",
            "name": "劳务管理服务标书模板（标准版）",
            "type": "劳务外包",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch6_qualifications", "ch8_after_sales"],
            "description": "劳务派遣/劳务管理服务专用模板，强调合规用工、全流程人员管理、劳动争议处理",
        },
        {
            "id": "tpl_labor_v2",
            "name": "岗位外包/业务外包标书模板",
            "type": "劳务外包",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch7_schedule"],
            "description": "岗位外包/业务流程外包模板，强调人员招聘、培训考核、驻场管理",
        },
        {
            "id": "tpl_labor_v3",
            "name": "食堂/后勤劳务外包标书模板",
            "type": "劳务外包",
            "sections": ["ch1_letter", "ch3_service", "ch5_staffing", "ch8_after_sales"],
            "description": "食堂劳务外包/后勤劳务外包模板，强调食品安全、人员健康证、应急保障",
        },
    ],
}


# ── Index Builder ──────────────────────────────────────────────────────


def build_template_index(
    index_dir: str | None = None,
) -> dict[str, VectorIndexManager]:
    """Build FAISS indices for all template types.

    Args:
        index_dir: Optional directory to save indices to

    Returns:
        Dict of {bid_type: VectorIndexManager}
    """
    embedder = MockEmbedder(dim=64)  # Small dim for testing
    indices: dict[str, VectorIndexManager] = {}

    for bid_type, templates in TEMPLATES.items():
        mgr = VectorIndexManager(dim=64)

        # Generate template vectors from description text
        ids = []
        vectors = []
        for tpl in templates:
            ids.append(tpl["id"])
            # Use description + sections for embedding
            text = tpl["description"] + " " + " ".join(tpl["sections"])
            vectors.append(embedder.embed_query(text))

        if vectors:
            mgr.add(ids, np.array(vectors, dtype=np.float32))

        indices[bid_type] = mgr
        logger.info(f"Built index for {bid_type}: {mgr.size} templates")

        # Save to disk if directory provided
        if index_dir:
            save_dir = Path(index_dir) / "faiss_index"
            save_dir.mkdir(parents=True, exist_ok=True)
            mgr.save(save_dir / f"templates_{bid_type}.npz")

    return indices


def get_template_types() -> list[str]:
    """Return all supported template types."""
    return list(TEMPLATES.keys())


def get_template_count(bid_type: str) -> int:
    """Return template count for a given type."""
    return len(TEMPLATES.get(bid_type, []))


def get_template_ids(bid_type: str) -> list[str]:
    """Return all template IDs for a given type."""
    return [t["id"] for t in TEMPLATES.get(bid_type, [])]


# ── Test verification ──────────────────────────────────────────────────


def test_all_types_indexed() -> bool:
    """Verify all 7 types have at least 3 templates. (T027 contract test)"""
    for bid_type in TEMPLATES:
        count = get_template_count(bid_type)
        if count < 3:
            return False
    return True
