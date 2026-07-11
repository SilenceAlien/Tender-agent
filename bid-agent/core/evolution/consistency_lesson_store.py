"""ConsistencyLessonStore — 持久化一致性经验存储。"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 路径解析 ─────────────────────────────────────────────────────────
# 基于 __file__ 解析绝对路径，避免 CWD 不同导致写入/读取不一致。
# bid-agent/core/evolution/ → 上溯 3 级到 bid-agent/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_STORE_PATH = _PROJECT_ROOT / "data" / "consistency_lessons.json"
_STORE_VERSION = 1
_SIMILARITY_THRESHOLD = 0.3

_SEVERITY_MAP = {
    "mutual_exclusion": "high",
    "amount_mismatch": "high",
    "number_mismatch": "medium",
    "date_mismatch": "medium",
    "document_composition_mismatch": "high",
    "format_violation": "medium",
    "legal_violation": "high",
}


class ConsistencyLessonStore:
    """一致性经验的持久化存储。"""

    def __init__(self, store_path: str | Path | None = None):
        # 默认使用基于 __file__ 解析的绝对路径，确保不同 CWD 下一致
        default_path = _DEFAULT_STORE_PATH
        self.store_path = Path(store_path) if store_path else Path(default_path)
        self._lessons: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            self._lessons = []
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._lessons = data.get("lessons", [])
            logger.info(f"Loaded {len(self._lessons)} consistency lessons")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load lessons: {e}")
            self._lessons = []

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump({"version": _STORE_VERSION, "lessons": self._lessons}, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _find_similar(self, issue_type: str, keywords: set[str], sections: list[str]) -> dict[str, Any] | None:
        for lesson in self._lessons:
            if lesson.get("issue_type") != issue_type:
                continue
            existing_kw = set(lesson.get("keywords", []))
            sim = self._jaccard(keywords, existing_kw)
            section_overlap = bool(set(sections) & set(lesson.get("applicable_sections", [])))
            # AND 逻辑：同时满足 keyword 相似度和章节重叠才合并。
            # 避免仅有章节重叠（如都涉及「投标函」）但问题完全不同就被错误合并。
            if sim >= _SIMILARITY_THRESHOLD and section_overlap:
                return lesson
            # 特殊情况：keywords 高度重合（≥0.6）时即使无章节重叠也合并
            if sim >= 0.6:
                return lesson
        return None

    def add_lesson(
        self,
        issue_type: str,
        title: str,
        description: str,
        rule: str,
        keywords: list[str] | None = None,
        applicable_sections: list[str] | None = None,
        applicable_bid_types: list[str] | None = None,
        directive_text: str = "",
        severity: str = "",
    ) -> str:
        """添加经验。相似经验则递增计数并合并。返回经验 ID。"""
        keywords = keywords or []
        applicable_sections = applicable_sections or []
        applicable_bid_types = applicable_bid_types or []
        severity = severity or _SEVERITY_MAP.get(issue_type, "medium")
        directive_text = directive_text or rule
        keyword_set = set(keywords)

        existing = self._find_similar(issue_type, keyword_set, applicable_sections)
        if existing:
            existing["occurrence_count"] = existing.get("occurrence_count", 0) + 1
            existing["last_seen"] = self._now()
            existing["keywords"] = sorted(set(existing.get("keywords", [])) | keyword_set)
            existing["applicable_sections"] = sorted(set(existing.get("applicable_sections", [])) | set(applicable_sections))
            existing["applicable_bid_types"] = sorted(set(existing.get("applicable_bid_types", [])) | set(applicable_bid_types))
            if len(description) > len(existing.get("description", "")):
                existing["description"] = description
            if len(rule) > len(existing.get("rule", "")):
                existing["rule"] = rule
            if len(directive_text) > len(existing.get("directive_text", "")):
                existing["directive_text"] = directive_text
            if severity == "high":
                existing["severity"] = "high"
            self._save()
            logger.info(f"Updated lesson {existing['id']} (count={existing['occurrence_count']})")
            return existing["id"]

        lesson_id = f"lesson_{uuid.uuid4().hex[:12]}"
        now = self._now()
        lesson = {
            "id": lesson_id,
            "issue_type": issue_type,
            "title": title,
            "description": description,
            "rule": rule,
            "keywords": sorted(keyword_set),
            "applicable_sections": sorted(applicable_sections),
            "applicable_bid_types": sorted(applicable_bid_types),
            "severity": severity,
            "directive_text": directive_text,
            "occurrence_count": 1,
            "first_seen": now,
            "last_seen": now,
        }
        self._lessons.append(lesson)
        self._save()
        logger.info(f"Added new consistency lesson: {lesson_id} ({title})")
        return lesson_id

    def get_all_lessons(self) -> list[dict[str, Any]]:
        """返回所有经验，按 occurrence_count 降序排列。"""
        return sorted(self._lessons, key=lambda x: x.get("occurrence_count", 0), reverse=True)

    def get_lessons_for_section(self, section_name: str, bid_type: str = "") -> list[dict[str, Any]]:
        """获取适用于指定章节的经验。"""
        result = []
        for lesson in self._lessons:
            applicable_types = lesson.get("applicable_bid_types", [])
            if applicable_types and bid_type and bid_type not in applicable_types:
                continue
            applicable_sections = lesson.get("applicable_sections", [])
            keywords = lesson.get("keywords", [])
            section_match = any(sec in section_name or section_name in sec for sec in applicable_sections)
            if not section_match:
                section_match = any(kw in section_name for kw in keywords)
            if section_match:
                result.append(lesson)
        return sorted(result, key=lambda x: x.get("occurrence_count", 0), reverse=True)

    def get_lessons_by_type(self, issue_type: str) -> list[dict[str, Any]]:
        """按 issue_type 筛选经验。"""
        return [l for l in self.get_all_lessons() if l.get("issue_type") == issue_type]

    def count(self) -> int:
        return len(self._lessons)

    def clear(self) -> None:
        """清空所有经验（用于测试）。"""
        self._lessons = []
        self._save()

    def build_directive_block(self, section_name: str, bid_type: str = "") -> str:
        """构建适用于指定章节的经验指令文本块，用于注入 prompt。"""
        lessons = self.get_lessons_for_section(section_name, bid_type)
        if not lessons:
            return ""
        lines = ["\n\n【一致性经验教训 — 基于历史问题自动学习，必须遵守】"]
        lines.append("以下规则来自系统自动学习的一致性问题教训，在生成本章节时必须严格遵守：\n")
        for i, lesson in enumerate(lessons, 1):
            count = lesson.get("occurrence_count", 1)
            lines.append(f"{i}. [{lesson.get('title', '未命名规则')}] (出现{count}次)")
            lines.append(f"   规则：{lesson.get('rule', '')}")
            lines.append(f"   指令：{lesson.get('directive_text', '')}\n")
        return "\n".join(lines)
