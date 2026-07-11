"""Integration tests for ReqExtractor node."""

import pytest

from core.nodes.req_extractor import (
    DEFAULT_FORMAT_RULES,
    _extract_json_from_response,
    _validate_requirements,
    req_extractor,
)
from core.state import NodeStatus, factory_state


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm_success():
    """Mock LLM that returns valid structured JSON."""
    import json

    def fn(prompt: str) -> str:
        return json.dumps({
            "scoring": [
                {"item_name": "技术方案", "score": 40, "criteria": "方案完整性、可行性和创新性"},
                {"item_name": "价格", "score": 30, "criteria": "最低价为基准价"},
                {"item_name": "公司资质", "score": 20, "criteria": "ISO认证、行业资质"},
                {"item_name": "售后服务", "score": 10, "criteria": "响应时间、服务网点"},
            ],
            "qualifications": [
                "具有独立法人资格",
                "注册资本不低于500万元",
                "近3年内有同类项目业绩",
            ],
            "tech_specs": [
                {"spec_name": "服务人员配置", "requirement": "配备不少于10名持证技术人员", "mandatory": True},
                {"spec_name": "信息化平台", "requirement": "具有劳务管理系统", "mandatory": False},
            ],
            "format_rules": {
                "page_margin": "上3.7cm/下3.5cm/左2.8cm/右2.6cm",
                "font": "正文仿宋_GB2312，标题黑体",
                "line_spacing": "28磅",
                "title_levels": ["黑体 二号", "楷体 三号", "仿宋 四号"],
                "seal_requirement": "逐页盖章",
                "binding": "A4胶装",
            },
        }, ensure_ascii=False)

    return fn


@pytest.fixture
def mock_llm_non_json():
    """Mock LLM that returns non-JSON text first, then valid JSON on retry."""

    call_count = [0]

    def fn(prompt: str) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "好的，以下是提取结果：好的，我分析了一下..."
        # Second call returns valid JSON
        import json
        return json.dumps({
            "scoring": [{"item_name": "价格", "score": 100, "criteria": "最低价中标"}],
            "qualifications": ["独立法人"],
            "tech_specs": [],
            "format_rules": {},
        }, ensure_ascii=False)

    return fn


@pytest.fixture
def mock_llm_always_bad():
    """Mock LLM that never returns valid JSON."""

    def fn(prompt: str) -> str:
        return "抱歉，我无法分析这份文档的内容。"

    return fn


# ── JSON Extraction Tests ──────────────────────────────────────────────


class TestExtractJson:
    def test_raw_json(self):
        result = _extract_json_from_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_code_block(self):
        result = _extract_json_from_response('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_markdown_code_without_label(self):
        result = _extract_json_from_response('```\n{"b": 2}\n```')
        assert result == {"b": 2}

    def test_json_amid_text(self):
        result = _extract_json_from_response('前面有一些文字{"x": [1, 2, 3]}后面也有一些')
        assert result == {"x": [1, 2, 3]}

    def test_invalid_json(self):
        result = _extract_json_from_response("这不是JSON")
        assert result is None

    def test_chinese_json(self):
        result = _extract_json_from_response('{"评分项": "技术方案", "分值": 40}')
        assert result == {"评分项": "技术方案", "分值": 40}


# ── Validation Tests ───────────────────────────────────────────────────


class TestValidateRequirements:
    def test_full_valid_input(self):
        raw = {
            "scoring": [{"item_name": "价格", "score": 30, "criteria": "最低价"}],
            "qualifications": ["资质A", "资质B"],
            "tech_specs": [{"spec_name": "规格X", "requirement": "要求X", "mandatory": True}],
            "format_rules": {"font": "宋体"},
        }
        result = _validate_requirements(raw)
        assert len(result["scoring"]) == 1
        assert result["scoring"][0]["item_name"] == "价格"
        assert result["scoring"][0]["score"] == 30
        assert result["qualifications"] == ["资质A", "资质B"]
        assert len(result["tech_specs"]) == 1
        # Custom font overrides default
        assert result["format_rules"]["font"] == "宋体"
        # Defaults still present
        assert "page_margin" in result["format_rules"]

    def test_empty_input_uses_defaults(self):
        result = _validate_requirements({})
        assert result["scoring"] == []
        assert result["qualifications"] == []
        assert result["tech_specs"] == []
        assert result["format_rules"] == DEFAULT_FORMAT_RULES

    def test_scoring_filters_invalid_items(self):
        raw = {
            "scoring": [
                "not a dict",
                {"item_name": "有效", "score": 10},
                {"no_name": "无效"},
            ]
        }
        result = _validate_requirements(raw)
        assert len(result["scoring"]) == 1
        assert result["scoring"][0]["item_name"] == "有效"

    def test_qualifications_filter_empty_strings(self):
        raw = {"qualifications": ["有效", "", None, "也有效"]}
        result = _validate_requirements(raw)
        assert result["qualifications"] == ["有效", "也有效"]

    def test_format_rules_merged_not_replaced(self):
        raw = {"format_rules": {"font": "楷体"}}
        result = _validate_requirements(raw)
        assert result["format_rules"]["font"] == "楷体"
        assert result["format_rules"]["line_spacing"] == "28磅"  # default preserved


# ── ReqExtractor Node Tests ────────────────────────────────────────────


class TestReqExtractorNode:
    def test_empty_documents(self):
        state = factory_state()
        result = req_extractor(state)
        assert result["node_status"]["ReqExtractor"] == NodeStatus.COMPLETED.value

    def test_with_successful_llm(self, mock_llm_success):
        state = factory_state(documents=[
            {"filename": "test.pdf", "parsed_content": "招标文件内容...", "type": "pdf"}
        ])
        result = req_extractor(state, llm_fn=mock_llm_success)
        assert result["node_status"]["ReqExtractor"] == NodeStatus.COMPLETED.value
        reqs = result["requirements"]
        assert len(reqs["scoring"]) == 4
        assert reqs["scoring"][0]["item_name"] == "技术方案"
        assert reqs["scoring"][0]["score"] == 40
        assert len(reqs["qualifications"]) == 3
        assert len(reqs["tech_specs"]) == 2
        assert reqs["format_rules"]["font"] == "正文仿宋_GB2312，标题黑体"

    def test_retry_on_bad_json_succeeds(self, mock_llm_non_json):
        state = factory_state(documents=[
            {"filename": "test.pdf", "parsed_content": "内容", "type": "pdf"}
        ])
        result = req_extractor(state, llm_fn=mock_llm_non_json)
        assert result["node_status"]["ReqExtractor"] == NodeStatus.COMPLETED.value
        assert len(result["requirements"]["scoring"]) == 1

    def test_always_bad_json_marks_failed(self, mock_llm_always_bad):
        state = factory_state(documents=[
            {"filename": "test.pdf", "parsed_content": "内容", "type": "pdf"}
        ])
        result = req_extractor(state, llm_fn=mock_llm_always_bad)
        assert result["node_status"]["ReqExtractor"] == NodeStatus.FAILED.value

    def test_no_llm_fn_returns_empty(self):
        """When no LLM is available, return empty requirements gracefully."""
        state = factory_state(documents=[
            {"filename": "test.pdf", "parsed_content": "内容", "type": "pdf"}
        ])
        result = req_extractor(state)  # no llm_fn
        assert result["node_status"]["ReqExtractor"] == NodeStatus.COMPLETED.value
        assert result["requirements"]["scoring"] == []

    def test_preserves_existing_requirements_on_failure(self, mock_llm_always_bad):
        existing = {
            "scoring": [{"item_name": "已有", "score": 50, "criteria": "之前提取的"}],
            "qualifications": [],
            "tech_specs": [],
            "format_rules": dict(DEFAULT_FORMAT_RULES),
        }
        state = factory_state(
            documents=[{"filename": "test.pdf", "parsed_content": "内容", "type": "pdf"}],
            requirements=existing,
        )
        result = req_extractor(state, llm_fn=mock_llm_always_bad)
        # On failure, preserves existing requirements
        assert result["requirements"] == existing

    def test_multiple_documents_concatenated(self, mock_llm_success):
        state = factory_state(documents=[
            {"filename": "a.pdf", "parsed_content": "文件A内容", "type": "pdf"},
            {"filename": "b.pdf", "parsed_content": "文件B内容", "type": "pdf"},
        ])
        result = req_extractor(state, llm_fn=mock_llm_success)
        assert result["node_status"]["ReqExtractor"] == NodeStatus.COMPLETED.value
