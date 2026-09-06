"""Unit tests for ConsistencyLessonStore and ConsistencyLessonExtractor."""

import json
import tempfile
from pathlib import Path

import pytest

from core.evolution.consistency_lesson_store import ConsistencyLessonStore
from core.evolution.consistency_lesson_extractor import ConsistencyLessonExtractor


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_store_path(tmp_path):
    """临时 JSON 文件路径用于测试持久化。"""
    return tmp_path / "consistency_lessons.json"


@pytest.fixture
def store(tmp_store_path):
    """干净的 ConsistencyLessonStore 实例。"""
    return ConsistencyLessonStore(store_path=tmp_store_path)


@pytest.fixture
def extractor(store):
    """使用临时 store 的 ConsistencyLessonExtractor（无 LLM）。"""
    return ConsistencyLessonExtractor(store=store)


# ── ConsistencyLessonStore 测试 ────────────────────────────────────────


class TestConsistencyLessonStore:
    def test_empty_store(self, store):
        assert store.count() == 0
        assert store.get_all_lessons() == []

    def test_add_new_lesson(self, store):
        lesson_id = store.add_lesson(
            issue_type="mutual_exclusion",
            title="投标保证金提交方式互斥",
            description="银行保函与银行转账/电汇是两种互斥的保证金提交方式",
            rule="同一章节中只能选择一种提交方式",
            keywords=["保证金", "保函", "转账"],
            applicable_sections=["投标保证金"],
        )
        assert lesson_id.startswith("lesson_")
        assert store.count() == 1

        lessons = store.get_all_lessons()
        assert lessons[0]["title"] == "投标保证金提交方式互斥"
        assert lessons[0]["occurrence_count"] == 1
        assert lessons[0]["severity"] == "high"  # mutual_exclusion → high

    def test_add_similar_lesson_increments_count(self, store):
        """相似经验应递增 occurrence_count 而非创建新条目。"""
        store.add_lesson(
            issue_type="amount_mismatch",
            title="跨章节金额不一致",
            description="投标函报价与分项报价表合计不一致",
            rule="所有金额必须一致",
            keywords=["金额", "报价", "保证金"],
            applicable_sections=["投标函", "分项报价表"],
        )
        # 添加相似经验（共享 keywords 和 sections）
        store.add_lesson(
            issue_type="amount_mismatch",
            title="报价金额差异",
            description="投标函与报价表金额不同",
            rule="报价金额须核对一致",
            keywords=["金额", "报价", "差异"],
            applicable_sections=["投标函"],
        )
        assert store.count() == 1  # 不应创建新条目
        lesson = store.get_all_lessons()[0]
        assert lesson["occurrence_count"] == 2

    def test_add_different_type_creates_new(self, store):
        """不同 issue_type 的经验应创建新条目。"""
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额不一致",
            description="desc1",
            rule="rule1",
            keywords=["金额"],
        )
        store.add_lesson(
            issue_type="number_mismatch",
            title="数量不一致",
            description="desc2",
            rule="rule2",
            keywords=["金额"],
        )
        assert store.count() == 2

    def test_get_lessons_for_section(self, store):
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="保证金互斥",
            description="desc",
            rule="rule",
            keywords=["保证金", "保函"],
            applicable_sections=["投标保证金"],
        )
        store.add_lesson(
            issue_type="number_mismatch",
            title="人员数量不一致",
            description="desc",
            rule="rule",
            keywords=["人员", "数量"],
            applicable_sections=["服务方案"],
        )

        # 查询保证金章节应只返回保证金经验
        lessons = store.get_lessons_for_section("投标保证金")
        assert len(lessons) == 1
        assert lessons[0]["title"] == "保证金互斥"

        # 查询服务方案应只返回人员经验
        lessons = store.get_lessons_for_section("服务方案")
        assert len(lessons) == 1
        assert lessons[0]["title"] == "人员数量不一致"

    def test_get_lessons_for_section_keyword_match(self, store):
        """keywords 中的关键词出现在 section_name 中也应匹配。"""
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额经验",
            description="desc",
            rule="rule",
            keywords=["报价"],
            applicable_sections=[],
        )
        lessons = store.get_lessons_for_section("分项报价表")
        assert len(lessons) == 1

    def test_bid_type_filter(self, store):
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="保证金",
            description="desc",
            rule="rule",
            keywords=["保证金"],
            applicable_sections=["投标保证金"],
            applicable_bid_types=["劳务管理服务类"],
        )
        # 匹配 bid_type
        lessons = store.get_lessons_for_section("投标保证金", bid_type="劳务管理服务类")
        assert len(lessons) == 1

        # 不匹配 bid_type
        lessons = store.get_lessons_for_section("投标保证金", bid_type="工程类")
        assert len(lessons) == 0

    def test_persistence(self, tmp_store_path):
        """经验应持久化到磁盘，重启后可用。"""
        store1 = ConsistencyLessonStore(store_path=tmp_store_path)
        store1.add_lesson(
            issue_type="mutual_exclusion",
            title="持久化测试",
            description="desc",
            rule="rule",
            keywords=["测试"],
            applicable_sections=["测试章节"],
        )
        assert store1.count() == 1

        # 重新加载
        store2 = ConsistencyLessonStore(store_path=tmp_store_path)
        assert store2.count() == 1
        assert store2.get_all_lessons()[0]["title"] == "持久化测试"

    def test_build_directive_block_empty(self, store):
        """无经验时返回空字符串。"""
        assert store.build_directive_block("任意章节") == ""

    def test_build_directive_block_with_lessons(self, store):
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="保证金互斥",
            description="desc",
            rule="只能选一种方式",
            keywords=["保证金"],
            applicable_sections=["投标保证金"],
            directive_text="保证金提交方式必须前后一致",
        )
        block = store.build_directive_block("投标保证金")
        assert "一致性经验教训" in block
        assert "保证金互斥" in block
        assert "保证金提交方式必须前后一致" in block

    def test_clear(self, store):
        store.add_lesson("amount_mismatch", "t", "d", "r")
        assert store.count() == 1
        store.clear()
        assert store.count() == 0

    def test_get_lessons_by_type(self, store):
        store.add_lesson(issue_type="amount_mismatch", title="t1", description="d", rule="r")
        store.add_lesson(issue_type="number_mismatch", title="t2", description="d", rule="r")
        store.add_lesson(issue_type="amount_mismatch", title="t3", description="d", rule="r")

        amount_lessons = store.get_lessons_by_type("amount_mismatch")
        assert len(amount_lessons) == 2
        number_lessons = store.get_lessons_by_type("number_mismatch")
        assert len(number_lessons) == 1

    def test_severity_auto_assign(self, store):
        """issue_type 应自动映射到默认 severity。"""
        store.add_lesson(issue_type="amount_mismatch", title="t", description="d", rule="r")
        assert store.get_all_lessons()[0]["severity"] == "high"

        store.add_lesson(issue_type="number_mismatch", title="t", description="d", rule="r")
        # number_mismatch → medium
        lessons = [l for l in store.get_all_lessons() if l["issue_type"] == "number_mismatch"]
        assert lessons[0]["severity"] == "medium"

    def test_merge_keywords_on_update(self, store):
        """更新相似经验时应合并 keywords（并集）。"""
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="t",
            description="d",
            rule="r",
            keywords=["保函", "转账"],
            applicable_sections=["投标保证金"],
        )
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="t2",
            description="d2",
            rule="r2",
            keywords=["电汇", "保函"],
            applicable_sections=["投标保证金"],
        )
        assert store.count() == 1
        lesson = store.get_all_lessons()[0]
        assert "保函" in lesson["keywords"]
        assert "转账" in lesson["keywords"]
        assert "电汇" in lesson["keywords"]


# ── ConsistencyLessonExtractor 测试 ────────────────────────────────────


class TestConsistencyLessonExtractor:
    def test_extract_from_cross_ref(self, extractor, store):
        """从 CrossReferenceChecker 的 inconsistencies 提取经验。"""
        inconsistencies = [
            {
                "type": "amount_mismatch",
                "field": "投标报价",
                "detail": "投标函中出现多个不同报价：{'¥1,280,000', '¥1,350,000'}",
                "sections": ["投标函", "分项报价表"],
                "severity": "high",
            },
            {
                "type": "number_mismatch",
                "field": "人员数量",
                "detail": "不同章节的总人数不一致：{'服务方案': 15, '人员配置': 12}",
                "sections": ["服务方案", "人员配置"],
                "severity": "medium",
            },
        ]
        ids = extractor.extract_from_cross_ref(inconsistencies, bid_type="劳务管理服务类")
        assert len(ids) == 2
        assert store.count() == 2

    def test_extract_from_compliance(self, extractor, store):
        """从 ComplianceChecker 的 format_issues 和 legal_issues 提取经验。"""
        format_issues = [{"detail": "页边距不符合招标文件要求"}]
        legal_issues = [{"detail": '使用了绝对化用语"全国第一"'}]
        ids = extractor.extract_from_compliance(format_issues, legal_issues)
        assert len(ids) == 2
        lessons = store.get_all_lessons()
        types = {l["issue_type"] for l in lessons}
        assert "format_violation" in types
        assert "legal_violation" in types

    def test_extract_from_quality(self, extractor, store):
        """从 QualityChecker 的 quality_report 提取一致性经验。"""
        quality_report = {
            "consistency": [
                "投标函中的承诺与后续章节内容矛盾",
                "投标有效期与招标文件要求不一致",
            ],
        }
        ids = extractor.extract_from_quality(quality_report)
        assert len(ids) >= 1
        assert store.count() >= 1

    def test_learn_from_state(self, extractor, store):
        """learn_from_state 应一次性处理所有报告。"""
        state = {
            "cross_ref_report": {
                "inconsistencies": [
                    {
                        "type": "amount_mismatch",
                        "detail": "报价金额不一致",
                        "sections": ["投标函"],
                    },
                ],
            },
            "compliance_report": {
                "format_issues": [{"detail": "格式不合规"}],
                "legal_issues": [],
            },
            "quality_report": {
                "consistency": ["承诺与内容矛盾"],
            },
            "bid_type": "劳务管理服务类",
        }
        ids = extractor.learn_from_state(state, bid_type="劳务管理服务类")
        assert len(ids) >= 2
        assert store.count() >= 2

    def test_learn_from_empty_state(self, extractor, store):
        """空状态不应产生经验。"""
        state = {
            "cross_ref_report": {},
            "compliance_report": {},
            "quality_report": {},
        }
        ids = extractor.learn_from_state(state)
        assert ids == []
        assert store.count() == 0

    def test_extract_skips_empty_detail(self, extractor, store):
        """空 detail 的条目应被跳过。"""
        inconsistencies = [
            {"type": "amount_mismatch", "detail": "", "sections": []},
            {"type": "amount_mismatch", "detail": "  ", "sections": []},
        ]
        ids = extractor.extract_from_cross_ref(inconsistencies)
        assert ids == []
        assert store.count() == 0

    def test_extract_with_llm(self, store):
        """LLM 模式应能从 LLM 返回的 JSON 中提取经验。"""

        def mock_llm(prompt: str) -> str:
            return json.dumps({
                "title": "金额一致性规则",
                "description": "报价金额必须全文一致",
                "rule": "所有章节的报价金额须以投标函为准",
                "keywords": ["报价", "金额", "投标函"],
                "directive_text": "生成任何涉及金额的章节时，须与投标函报价核对一致",
            }, ensure_ascii=False)

        extractor = ConsistencyLessonExtractor(store=store, llm_fn=mock_llm)
        lesson_id = extractor._extract_one(
            "amount_mismatch",
            "投标函报价与分项报价表不一致",
            ["投标函", "分项报价表"],
        )
        assert lesson_id is not None
        lesson = store.get_all_lessons()[0]
        assert lesson["title"] == "金额一致性规则"
        assert "投标函" in lesson["rule"]

    def test_extract_llm_fallback_to_template(self, store):
        """LLM 返回非 JSON 时应回退到规则模板。"""

        def bad_llm(prompt: str) -> str:
            return "这不是JSON"

        extractor = ConsistencyLessonExtractor(store=store, llm_fn=bad_llm)
        lesson_id = extractor._extract_one(
            "amount_mismatch",
            "报价金额不一致",
            ["投标函"],
        )
        assert lesson_id is not None
        lesson = store.get_all_lessons()[0]
        # 应使用模板的默认 rule
        assert "投标报价" in lesson["rule"] or "金额" in lesson["rule"]


# ── 集成测试：section_generator 注入 ────────────────────────────────────


class TestSectionGeneratorInjection:
    def test_prompt_includes_lessons(self, tmp_store_path, monkeypatch):
        """section_generator 的 prompt 应包含一致性经验。"""
        # 准备经验库
        store = ConsistencyLessonStore(store_path=tmp_store_path)
        store.add_lesson(
            issue_type="mutual_exclusion",
            title="保证金互斥",
            description="保函与转账混用",
            rule="只能选一种方式",
            keywords=["保证金", "保函"],
            applicable_sections=["投标保证金"],
            directive_text="保证金提交方式必须前后一致，严禁混用",
        )

        # Monkeypatch ConsistencyLessonStore 的默认路径
        monkeypatch.setattr(
            "core.evolution.consistency_lesson_store._DEFAULT_STORE_PATH",
            str(tmp_store_path),
        )

        from core.nodes.section_generator import _get_consistency_lesson_directive
        directive = _get_consistency_lesson_directive("sec4_deposit", "劳务管理服务类")
        assert "保证金" in directive or "一致性经验" in directive

    def test_prompt_empty_when_no_lessons(self, tmp_store_path, monkeypatch):
        """无经验时指令块应为空。"""
        monkeypatch.setattr(
            "core.evolution.consistency_lesson_store._DEFAULT_STORE_PATH",
            str(tmp_store_path),
        )
        from core.nodes.section_generator import _get_consistency_lesson_directive
        directive = _get_consistency_lesson_directive("sec4_deposit", "劳务管理服务类")
        assert directive == ""


# ── 集成测试：feedback_processor 触发学习 ──────────────────────────────


class TestFeedbackProcessorLearning:
    def test_feedback_triggers_learning(self, tmp_store_path, monkeypatch):
        """FeedbackProcessor 处理一致性问题时应触发经验学习。"""
        monkeypatch.setattr(
            "core.evolution.consistency_lesson_store._DEFAULT_STORE_PATH",
            str(tmp_store_path),
        )

        from core.nodes.feedback_processor import feedback_processor
        from core.state import factory_state

        state = factory_state()
        state["cross_ref_report"] = {
            "verdict": "FAIL",
            "inconsistencies": [
                {
                    "type": "amount_mismatch",
                    "detail": "投标函报价与分项报价表合计不一致",
                    "sections": ["投标函", "分项报价表"],
                    "severity": "high",
                },
            ],
        }
        state["sections"] = {"投标函": "测试内容", "分项报价表": "测试内容2"}

        result = feedback_processor(state)

        # 验证经验已被学习
        store = ConsistencyLessonStore(store_path=tmp_store_path)
        assert store.count() >= 1
        lessons = store.get_all_lessons()
        assert any(l["issue_type"] == "amount_mismatch" for l in lessons)


# ── 回归测试：验证 code-review 修复 ─────────────────────────────────────


class TestCodeReviewFixes:
    """验证 code-review 发现的 5 个问题的修复。"""

    def test_fix1_absolute_store_path(self):
        """发现1修复：_DEFAULT_STORE_PATH 应为绝对路径，不依赖 CWD。"""
        from core.evolution.consistency_lesson_store import _DEFAULT_STORE_PATH
        path = Path(_DEFAULT_STORE_PATH)
        assert path.is_absolute(), f"Store path should be absolute, got: {path}"
        assert "consistency_lessons.json" in str(path)

    def test_fix2_quality_issue_type_classification(self, store):
        """发现2修复：extract_from_quality 应智能分类 issue_type，不硬编码 mutual_exclusion。"""
        extractor = ConsistencyLessonExtractor(store=store)
        # 包含「金额」「报价」的问题应被分类为 amount_mismatch
        quality_report = {"consistency": ["投标函报价金额不一致"]}
        ids = extractor.extract_from_quality(quality_report, sections={"投标函": "content"})
        assert len(ids) == 1
        lesson = store.get_all_lessons()[0]
        assert lesson["issue_type"] == "amount_mismatch"

    def test_fix2_quality_issue_type_date(self, store):
        """发现2修复：包含「日期」的问题应被分类为 date_mismatch。"""
        extractor = ConsistencyLessonExtractor(store=store)
        quality_report = {"consistency": ["投标有效期日期不一致"]}
        ids = extractor.extract_from_quality(quality_report, sections={"投标函": "content"})
        assert len(ids) == 1
        lesson = store.get_all_lessons()[0]
        assert lesson["issue_type"] == "date_mismatch"

    def test_fix2_quality_extracts_sections(self, store):
        """发现2修复：extract_from_quality 应从问题文本中提取关联章节。"""
        extractor = ConsistencyLessonExtractor(store=store)
        quality_report = {"consistency": ["投标函中的报价与分项报价表不一致"]}
        sections = {"投标函": "content", "分项报价表": "content2"}
        ids = extractor.extract_from_quality(quality_report, sections=sections)
        assert len(ids) == 1
        lesson = store.get_all_lessons()[0]
        # 应提取到章节名
        assert "投标函" in lesson["applicable_sections"] or "分项报价表" in lesson["applicable_sections"]

    def test_fix3_no_overmerge_on_section_overlap_only(self, store):
        """发现3修复：仅章节重叠但 keyword 不相似时不应合并。"""
        # 经验A：金额问题，涉及投标函
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额不一致",
            description="报价金额不同",
            rule="金额须一致",
            keywords=["金额", "报价", "万元"],
            applicable_sections=["投标函"],
        )
        # 经验B：也是 amount_mismatch，也涉及投标函，但 keywords 完全不同
        store.add_lesson(
            issue_type="amount_mismatch",
            title="税率不一致",
            description="增值税税率不同",
            rule="税率须一致",
            keywords=["税率", "增值税"],
            applicable_sections=["投标函"],
        )
        # 两条经验不应被合并（keyword Jaccard=0，仅有章节重叠）
        assert store.count() == 2

    def test_fix3_merge_on_high_keyword_similarity(self, store):
        """M12 修复（fix_M_summary.md）：去重为「Jaccard≥阈值 AND 章节重叠」双重条件。

        keyword 高度重合（Jaccard=0.75）但章节无重叠时，依据 §4.3 AND 语义
        不应合并——旧「sim≥0.6 无章节重叠也合并」分支已在 M12 修复中删除。
        """
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额不一致",
            description="d1",
            rule="r1",
            keywords=["金额", "报价", "保证金"],
            applicable_sections=["投标函"],
        )
        # keywords 高度重合，但 sections 不同（无重叠）
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额问题",
            description="d2",
            rule="r2",
            keywords=["金额", "报价", "保证金", "元"],
            applicable_sections=["分项报价表"],
        )
        # AND 语义：无章节重叠 → 不合并，各自保持独立
        assert store.count() == 2

        # 对照组：keywords 高度重合「且」章节重叠 → 合并
        store.add_lesson(
            issue_type="amount_mismatch",
            title="金额问题2",
            description="d3",
            rule="r3",
            keywords=["金额", "报价", "保证金"],
            applicable_sections=["投标函", "开标一览表"],
        )
        # Jaccard=1.0 ≥ 阈值 且与经验A章节重叠（投标函）→ 与经验A合并
        assert store.count() == 2
        assert store.get_all_lessons()[0]["occurrence_count"] == 2

    def test_fix4_no_noise_keywords(self, store):
        """发现4修复：_extract_with_template 不应从 detail 中提取无意义中文子串。"""
        extractor = ConsistencyLessonExtractor(store=store)
        detail = "投标函报价与分项报价表合计不一致"
        extractor._extract_with_template("amount_mismatch", detail, ["投标函"], "")
        lesson = store.get_all_lessons()[0]
        # keywords 应仅包含模板预定义的，不应有 detail 中提取的噪声子串
        for kw in lesson["keywords"]:
            assert kw in ["金额", "报价", "保证金", "万元", "元", "¥", "不一致"], \
                f"Unexpected noise keyword: {kw}"

    def test_fix5_no_llm_by_default(self, tmp_store_path, monkeypatch):
        """发现5修复：_learn_consistency_lessons 默认不传 llm_fn 给 extractor。"""
        monkeypatch.setattr(
            "core.evolution.consistency_lesson_store._DEFAULT_STORE_PATH",
            str(tmp_store_path),
        )
        # 确保 BID_CONSISTENCY__LLM_EXTRACT 未设置
        import os
        monkeypatch.delenv("BID_CONSISTENCY__LLM_EXTRACT", raising=False)

        llm_called = False

        def tracking_llm(prompt: str) -> str:
            nonlocal llm_called
            llm_called = True
            return '{"title": "t", "description": "d", "rule": "r", "keywords": [], "directive_text": ""}'

        from core.nodes.feedback_processor import _learn_consistency_lessons
        from core.state import factory_state

        state = factory_state()
        state["cross_ref_report"] = {
            "verdict": "FAIL",
            "inconsistencies": [
                {"type": "amount_mismatch", "detail": "报价不一致", "sections": ["投标函"]},
            ],
        }
        state["sections"] = {"投标函": "content"}

        # 即使传了 llm_fn，默认也不应调用 LLM
        _learn_consistency_lessons(state, llm_fn=tracking_llm)
        assert not llm_called, "LLM should not be called by default"

    def test_fix5_llm_when_explicitly_enabled(self, tmp_store_path, monkeypatch):
        """发现5修复：设置环境变量后可显式启用 LLM 提取。"""
        monkeypatch.setattr(
            "core.evolution.consistency_lesson_store._DEFAULT_STORE_PATH",
            str(tmp_store_path),
        )
        monkeypatch.setenv("BID_CONSISTENCY__LLM_EXTRACT", "true")

        llm_called = False

        def tracking_llm(prompt: str) -> str:
            nonlocal llm_called
            llm_called = True
            return json.dumps({
                "title": "测试", "description": "d", "rule": "r",
                "keywords": ["测试"], "directive_text": "dt",
            }, ensure_ascii=False)

        from core.nodes.feedback_processor import _learn_consistency_lessons
        from core.state import factory_state

        state = factory_state()
        state["cross_ref_report"] = {
            "verdict": "FAIL",
            "inconsistencies": [
                {"type": "amount_mismatch", "detail": "报价不一致", "sections": ["投标函"]},
            ],
        }
        state["sections"] = {"投标函": "content"}

        _learn_consistency_lessons(state, llm_fn=tracking_llm)
        assert llm_called, "LLM should be called when explicitly enabled"
