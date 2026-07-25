"""PromptRegistry — stores high-scoring prompt variants for reuse.

When QualityChecker gives a section PASS + high completeness score,
the prompt variant (with context) is saved here. SectionGenerator then
retrieves the best one next time it generates the same section type.

Schema (SQLite table: prompt_registry):
    id TEXT PRIMARY KEY (UUID)
    section_name TEXT NOT NULL
    bid_type TEXT NOT NULL        -- e.g. "服务", "货物"
    prompt_text TEXT NOT NULL     -- The prompt template used
    source_context TEXT           -- JSON: {doc_type, requirements snapshot}
    completeness_score REAL       -- 0.0–1.0
    quality_verdict TEXT          -- "PASS" | "FAIL"
    template_id TEXT              -- Matched template ID, for context
    used_count INTEGER DEFAULT 0  -- Times this variant was selected
    avg_score REAL                -- Running average across uses
    created_at TEXT NOT NULL      -- ISO 8601
    updated_at TEXT NOT NULL
)

─── L9 接入状态（code-audit / cluster_C7_v2.md）─────────────────────────
本模块当前【未被任何 pipeline 节点调用】——属于「已实现、未接线」的死代码
（Grep 全仓仅 __init__.py 导出、tests 实例化，无节点 import/调用本类，
且无任何 .save() 写入路径）。自进化闭环实际只走 ConsistencyLessonStore 注入。

与 ConsistencyLessonStore 注入路径的关系（并行、非重叠）：
  · ConsistencyLessonStore：问题→结构化「指令规则」(directive)，注入 prompt 说
    “不要怎么做 / 要保持一致”，由 FeedbackProcessor→Extractor→Store→SectionGenerator 接通（H11 已修）。
  · PromptRegistry：保存「整段高分 prompt 变体」(prompt_text) 并按历史表现回灌，
    机制不同（选最优 prompt 变体），具备 Store 没有的独特价值，但本仓从未被写入。

若未来需接入（建议【不要在此版本接线】，避免与 Store 注入冲突/引入风险）：
  1) 写入点：在 QualityChecker 给出 PASS + 高 completeness_score 的章节处调用
     self.save(section_name, bid_type, prompt_text=该章节实际构造的 prompt,
               completeness_score=..., quality_verdict="PASS")。注意需明确
     prompt_text 的来源（当前 _get_section_prompt 不回传它），改动点 > 20 行。
  2) 读取点：在 core/nodes/section_generator.py 的 _get_section_prompt 末尾、
     ConsistencyLessonStore 注入之后，调 PromptSelector.inject_into_prompt(...)
     做变体回灌——且必须确保 injection_mode != "replace"（replace 会整体覆盖
     已接好的 契约/经验/RAG/反编造 结构化 prompt，风险高）。
  3) Registry 默认空库：未写入时 Selector 始终返回 [] → 降级为原 base_prompt，
     故「只接读取不接写入」为永久空操作、无收益，必须同时接写入才有意义。
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────

PROMPT_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_registry (
    id TEXT PRIMARY KEY,
    section_name TEXT NOT NULL,
    bid_type TEXT NOT NULL DEFAULT '',
    prompt_text TEXT NOT NULL,
    source_context TEXT DEFAULT '{}',
    completeness_score REAL DEFAULT 0.0,
    quality_verdict TEXT DEFAULT 'PASS',
    template_id TEXT DEFAULT '',
    used_count INTEGER DEFAULT 0,
    avg_score REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pr_section_type
    ON prompt_registry(section_name, bid_type);
CREATE INDEX IF NOT EXISTS idx_pr_score
    ON prompt_registry(completeness_score DESC);
"""

# ── Core ───────────────────────────────────────────────────────────────


class PromptRegistry:
    """Manages the prompt evolution database."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(PROMPT_REGISTRY_SCHEMA)
        self._conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        assert self._conn is not None, "DB not initialized"
        return self._conn

    # ── Write ──────────────────────────────────────────────────────────

    def save(
        self,
        section_name: str,
        bid_type: str,
        prompt_text: str,
        completeness_score: float = 0.0,
        quality_verdict: str = "PASS",
        source_context: dict | None = None,
        template_id: str = "",
    ) -> str:
        """Save a prompt variant. Only call when verdict == PASS.

        Returns the new entry ID.
        """
        prompt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO prompt_registry
                   (id, section_name, bid_type, prompt_text, source_context,
                    completeness_score, quality_verdict, template_id,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prompt_id,
                    section_name,
                    bid_type,
                    prompt_text,
                    json.dumps(source_context or {}, ensure_ascii=False),
                    completeness_score,
                    quality_verdict,
                    template_id,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            pass

        logger.info(
            f"Saved prompt for {section_name}/{bid_type} "
            f"(score={completeness_score:.2f})"
        )
        return prompt_id

    # ── Read ───────────────────────────────────────────────────────────

    def get_best(
        self,
        section_name: str,
        bid_type: str = "",
        min_score: float = 0.7,
        limit: int = 3,
    ) -> list[dict]:
        """Get the best prompt variants for a section.

        Returns top-N by completeness_score descending.
        If bid_type is empty, matches any type.
        """
        conn = self._get_conn()
        try:
            if bid_type:
                rows = conn.execute(
                    """SELECT * FROM prompt_registry
                       WHERE section_name = ? AND bid_type = ?
                         AND completeness_score >= ?
                         AND quality_verdict = 'PASS'
                       ORDER BY completeness_score DESC, used_count DESC
                       LIMIT ?""",
                    (section_name, bid_type, min_score, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM prompt_registry
                       WHERE section_name = ?
                         AND completeness_score >= ?
                         AND quality_verdict = 'PASS'
                       ORDER BY completeness_score DESC, used_count DESC
                       LIMIT ?""",
                    (section_name, min_score, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            pass

    def mark_used(self, prompt_id: str, new_score: float | None = None) -> None:
        """Increment used_count and optionally update avg_score."""
        conn = self._get_conn()
        try:
            if new_score is not None:
                # BUG-06 fix: SQLite SET clauses execute left-to-right, so
                # avg_score must be computed BEFORE used_count is incremented.
                # Otherwise used_count is already (old+1) when avg_score reads it,
                # producing (old_avg * (old+1) + new) / (old+2) instead of
                # (old_avg * old + new) / (old+1).
                conn.execute(
                    """UPDATE prompt_registry
                       SET avg_score = (avg_score * used_count + ?) / (used_count + 1),
                           used_count = used_count + 1,
                           updated_at = ?
                       WHERE id = ?""",
                    (new_score, datetime.now(timezone.utc).isoformat(), prompt_id),
                )
            else:
                conn.execute(
                    """UPDATE prompt_registry
                       SET used_count = used_count + 1,
                           updated_at = ?
                       WHERE id = ?""",
                    (datetime.now(timezone.utc).isoformat(), prompt_id),
                )
            conn.commit()
        finally:
            pass

    def count(self, section_name: str | None = None, bid_type: str | None = None) -> int:
        """Count stored prompts, optionally filtered."""
        conn = self._get_conn()
        try:
            if section_name and bid_type:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM prompt_registry WHERE section_name = ? AND bid_type = ?",
                    (section_name, bid_type),
                ).fetchone()
            elif section_name:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM prompt_registry WHERE section_name = ?",
                    (section_name,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as c FROM prompt_registry").fetchone()
            return row["c"] if row else 0
        finally:
            pass

    def get_top_scoring_sections(self, limit: int = 10) -> list[dict]:
        """Get sections ranked by average completeness_score."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT section_name, bid_type,
                          AVG(completeness_score) as avg_score,
                          COUNT(*) as count
                   FROM prompt_registry
                   GROUP BY section_name, bid_type
                   ORDER BY avg_score DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            pass
