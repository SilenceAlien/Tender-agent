"""Unit tests for MetricsTracker."""

import pytest

from core.evolution.metrics_tracker import MetricsTracker


@pytest.fixture
def tracker():
    return MetricsTracker(db_path=":memory:")


@pytest.fixture
def populated_tracker(tracker):
    # Project 1, Section A: passed on first try
    tracker.record("proj_1", "技术方案", 0, 0.95, "PASS", [], 10, 10)
    # Project 1, Section B: failed twice then passed
    tracker.record("proj_1", "资质", 0, 0.55, "FAIL", ["资质描述不够详细"], 5, 2)
    tracker.record("proj_1", "资质", 1, 0.72, "FAIL", ["缺少ISO证书编号"], 5, 3)
    tracker.record("proj_1", "资质", 2, 0.88, "PASS", [], 5, 5)
    # Project 2, Section A: passed first try
    tracker.record("proj_2", "技术方案", 0, 0.92, "PASS", [], 10, 9)
    return tracker


class TestMetricsTracker:
    def test_record_returns_uuid(self, tracker):
        rid = tracker.record("p1", "测试章节", 0, 0.85, "PASS")
        assert len(rid) == 36

    def test_section_first_pass_rate(self, populated_tracker):
        result = populated_tracker.section_first_pass_rate("技术方案")
        assert result["section_name"] == "技术方案"
        assert result["total_runs"] == 2
        assert result["first_pass_count"] == 2
        assert result["first_pass_rate"] == 1.0  # 100%

    def test_section_first_pass_rate_mixed(self, populated_tracker):
        result = populated_tracker.section_first_pass_rate("资质")
        assert result["total_runs"] == 3
        assert result["first_pass_count"] == 0
        assert result["first_pass_rate"] == 0.0

    def test_avg_revision_rounds(self, populated_tracker):
        result = populated_tracker.avg_revision_rounds("资质")
        assert result["max_rounds"] == 2

    def test_top_failure_reasons(self, populated_tracker):
        reasons = populated_tracker.top_failure_reasons()
        assert len(reasons) >= 1
        assert any("资质描述不够详细" in r["reason"] for r in reasons)

    def test_top_failure_reasons_by_section(self, populated_tracker):
        reasons = populated_tracker.top_failure_reasons("资质")
        assert len(reasons) == 2

    def test_quality_trend(self, populated_tracker):
        trend = populated_tracker.quality_trend("资质")
        assert len(trend) == 3
        # Scores should improve over rounds
        scores = [r["completeness_score"] for r in trend]
        assert scores[-1] > scores[0]  # last round > first round

    def test_quality_trend_chronological(self, populated_tracker):
        trend = populated_tracker.quality_trend("资质")
        timestamps = [r["recorded_at"] for r in trend]
        assert timestamps == sorted(timestamps)

    def test_overall_summary(self, populated_tracker):
        summary = populated_tracker.overall_summary()
        assert summary["total_records"] == 5
        assert len(summary["sections"]) == 2

        # Find "资质" section
        zizhi = next(s for s in summary["sections"] if s["section_name"] == "资质")
        assert zizhi["runs"] == 3
        assert zizhi["pass_rate"] < 1.0

    def test_empty_tracker(self, tracker):
        summary = tracker.overall_summary()
        assert summary["total_records"] == 0

    def test_failure_reasons_stored(self, tracker):
        reasons = ["缺少证书", "格式错误", "内容不完整"]
        tracker.record("p1", "测试", 0, 0.5, "FAIL", reasons, 10, 5)
        found = tracker.top_failure_reasons("测试")
        assert len(found) == 3

    def test_project_isolation(self, tracker):
        tracker.record("p1", "章节A", 0, 0.9, "PASS")
        tracker.record("p2", "章节A", 0, 0.5, "FAIL", ["原因1"], 10, 3)

        # Filtered by project
        result_p1 = tracker.section_first_pass_rate("章节A", "p1")
        assert result_p1["first_pass_rate"] == 1.0

        result_p2 = tracker.section_first_pass_rate("章节A", "p2")
        assert result_p2["first_pass_rate"] == 0.0
