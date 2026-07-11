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
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Sequence

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
