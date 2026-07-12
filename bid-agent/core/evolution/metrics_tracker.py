"""MetricsTracker — tracks quality trends per section across runs.

Tracks:
    - Per-section first-pass rate
    - Average revision rounds
    - Top failure reasons (extracted from QualityChecker reports)
    - Trend over time (is quality improving?)

Schema (SQLite table: evolution_metrics):
    id TEXT PRIMARY KEY
    project_id TEXT NOT NULL
    section_name TEXT NOT NULL
    round_num INTEGER NOT NULL      -- Which revision round
    completeness_score REAL         -- 0.0–1.0
    verdict TEXT                    -- "PASS" | "FAIL"
    failure_reasons TEXT            -- JSON list of reasons
    total_items INTEGER
    passed_items INTEGER
    node_status TEXT                -- JSON: overall node status
    recorded_at TEXT NOT NULL       -- ISO 8601
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────

METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_metrics (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    section_name TEXT NOT NULL,
    round_num INTEGER NOT NULL DEFAULT 0,
    completeness_score REAL DEFAULT 0.0,
    verdict TEXT DEFAULT 'PASS',
    failure_reasons TEXT DEFAULT '[]',
    total_items INTEGER DEFAULT 0,
    passed_items INTEGER DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_em_project
    ON evolution_metrics(project_id);
CREATE INDEX IF NOT EXISTS idx_em_section
    ON evolution_metrics(section_name);
CREATE INDEX IF NOT EXISTS idx_em_verdict
    ON evolution_metrics(verdict);
CREATE INDEX IF NOT EXISTS idx_em_recorded
    ON evolution_metrics(recorded_at);
"""


class MetricsTracker:
    """Tracks and analyzes quality metrics for the evolution system."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(METRICS_SCHEMA)
        self._conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        assert self._conn is not None, "DB not initialized"
        return self._conn

    # ── Record ─────────────────────────────────────────────────────────

    def record(
        self,
        project_id: str,
        section_name: str,
        round_num: int,
        completeness_score: float = 0.0,
        verdict: str = "PASS",
        failure_reasons: list[str] | None = None,
        total_items: int = 0,
        passed_items: int = 0,
    ) -> str:
        """Record a quality check result for one section."""
        metric_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO evolution_metrics
                   (id, project_id, section_name, round_num,
                    completeness_score, verdict, failure_reasons,
                    total_items, passed_items, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metric_id,
                    project_id,
                    section_name,
                    round_num,
                    completeness_score,
                    verdict,
                    json.dumps(failure_reasons or [], ensure_ascii=False),
                    total_items,
                    passed_items,
                    now,
                ),
            )
            conn.commit()
        finally:
            pass

        return metric_id

    # ── Query ──────────────────────────────────────────────────────────

    def section_first_pass_rate(
        self, section_name: str, project_id: str = ""
    ) -> dict[str, Any]:
        """Calculate first-pass rate for a section.

        Returns: {total_runs, first_pass_count, first_pass_rate}
        """
        conn = self._get_conn()
        try:
            if project_id:
                rows = conn.execute(
                    """SELECT verdict, round_num FROM evolution_metrics
                       WHERE section_name = ? AND project_id = ?
                       ORDER BY recorded_at""",
                    (section_name, project_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT verdict, round_num FROM evolution_metrics
                       WHERE section_name = ?
                       ORDER BY recorded_at""",
                    (section_name,),
                ).fetchall()

            total = len(rows)
            first_pass = sum(
                1 for r in rows if r["verdict"] == "PASS" and r["round_num"] == 0
            )
            return {
                "section_name": section_name,
                "total_runs": total,
                "first_pass_count": first_pass,
                "first_pass_rate": first_pass / total if total > 0 else 0.0,
            }
        finally:
            pass

    def avg_revision_rounds(
        self, section_name: str, project_id: str = ""
    ) -> dict[str, Any]:
        """Calculate average revision rounds for a section.

        Returns: {section_name, total_sessions, avg_rounds, max_rounds}

        BUG-07 fix: Previously used (max_round + 1) / 2.0 which assumes uniform
        distribution — statistically incorrect. Now queries each session (a
        session starts at round_num=0) and computes the actual average of
        per-session max round_num.
        """
        conn = self._get_conn()
        try:
            if project_id:
                rows = conn.execute(
                    """SELECT round_num, recorded_at
                       FROM evolution_metrics
                       WHERE section_name = ? AND project_id = ?
                       ORDER BY recorded_at""",
                    (section_name, project_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT round_num, recorded_at
                       FROM evolution_metrics
                       WHERE section_name = ?
                       ORDER BY recorded_at""",
                    (section_name,),
                ).fetchall()

            # Group records into sessions. Each session starts at round_num=0.
            # The max round_num within a session = number of revisions for that session.
            session_max_rounds: list[int] = []
            current_max = 0
            in_session = False

            for row in rows:
                rn = row["round_num"] if row["round_num"] is not None else 0
                if rn == 0:
                    # New session starts
                    if in_session:
                        session_max_rounds.append(current_max)
                    current_max = 0
                    in_session = True
                else:
                    current_max = max(current_max, rn)

            # Don't forget the last session
            if in_session:
                session_max_rounds.append(current_max)

            total_sessions = len(session_max_rounds)
            max_r = max(session_max_rounds) if session_max_rounds else 0
            avg = sum(session_max_rounds) / total_sessions if total_sessions > 0 else 0.0

            return {
                "section_name": section_name,
                "total_sessions": total_sessions,
                "avg_rounds": round(avg, 2),
                "max_rounds": max_r,
            }
        finally:
            pass

    def top_failure_reasons(
        self, section_name: str = "", limit: int = 10
    ) -> list[dict]:
        """Get the most common failure reasons."""
        conn = self._get_conn()
        try:
            if section_name:
                rows = conn.execute(
                    """SELECT failure_reasons FROM evolution_metrics
                       WHERE section_name = ? AND verdict = 'FAIL'""",
                    (section_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT failure_reasons FROM evolution_metrics
                       WHERE verdict = 'FAIL'"""
                ).fetchall()

            # Aggregate failure reasons
            reason_counts: dict[str, int] = {}
            for row in rows:
                reasons = json.loads(row["failure_reasons"] or "[]")
                for reason in reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1

            sorted_reasons = sorted(
                reason_counts.items(), key=lambda x: x[1], reverse=True
            )
            return [
                {"reason": r, "count": c}
                for r, c in sorted_reasons[:limit]
            ]
        finally:
            pass

    def quality_trend(
        self, section_name: str = "", last_n: int = 20
    ) -> list[dict]:
        """Get quality score trend over time.

        Returns list of {recorded_at, completeness_score, verdict}.
        """
        conn = self._get_conn()
        try:
            if section_name:
                rows = conn.execute(
                    """SELECT recorded_at, completeness_score, verdict, round_num
                       FROM evolution_metrics
                       WHERE section_name = ?
                       ORDER BY recorded_at DESC
                       LIMIT ?""",
                    (section_name, last_n),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT recorded_at, completeness_score, verdict, round_num
                       FROM evolution_metrics
                       ORDER BY recorded_at DESC
                       LIMIT ?""",
                    (last_n,),
                ).fetchall()

            return [dict(r) for r in reversed(rows)]  # chronological
        finally:
            pass

    def overall_summary(self) -> dict[str, Any]:
        """Get an overall evolution quality summary."""
        conn = self._get_conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM evolution_metrics"
            ).fetchone()["c"]

            if total == 0:
                return {"total_records": 0, "sections": []}

            # Per-section summary
            rows = conn.execute(
                """SELECT section_name,
                          COUNT(*) as runs,
                          AVG(completeness_score) as avg_score,
                          SUM(CASE WHEN verdict = 'PASS' THEN 1 ELSE 0 END) as passes,
                          SUM(CASE WHEN round_num = 0 AND verdict = 'PASS' THEN 1 ELSE 0 END) as first_passes
                   FROM evolution_metrics
                   GROUP BY section_name
                   ORDER BY avg_score DESC"""
            ).fetchall()

            sections = []
            for r in rows:
                sections.append({
                    "section_name": r["section_name"],
                    "runs": r["runs"],
                    "avg_score": round(r["avg_score"], 3),
                    "pass_rate": r["passes"] / r["runs"] if r["runs"] > 0 else 0.0,
                    "first_pass_rate": r["first_passes"] / r["runs"] if r["runs"] > 0 else 0.0,
                })

            return {"total_records": total, "sections": sections}
        finally:
            pass
