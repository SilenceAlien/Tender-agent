"""Tests for document composition consistency between bid letter and actual sections.

Verifies:
1. The bid letter declares 分项报价表 as part of the document, and the
   generated sections include a corresponding 分项报价表 section.
2. The cross-reference checker catches when a declared item has no
   corresponding section (the original bug).
3. The cross-reference checker passes when all declared items are present.
4. OPTIMIZED_SECTIONS includes the 分项报价表 section.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core.nodes.cross_reference_checker import (
    _check_document_composition_consistency,
    cross_reference_checker,
)
from core.nodes.optimized_prompts import OPTIMIZED_SECTIONS, OPTIMIZED_SECTION_PROMPTS
from core.state import factory_state


# ── Section structure tests ─────────────────────────────────────────────


class TestOptimizedSectionsIncludePriceTable:
    """Verify OPTIMIZED_SECTIONS includes the 分项报价表 section."""

    def test_price_table_section_exists(self):
        """OPTIMIZED_SECTIONS should include a section with '分项报价表' in its name."""
        names = [s["name"] for s in OPTIMIZED_SECTIONS]
        assert any("分项报价表" in n for n in names), (
            f"OPTIMIZED_SECTIONS missing 分项报价表: {names}"
        )

    def test_price_table_section_key_exists(self):
        """OPTIMIZED_SECTION_PROMPTS should have a prompt for the price table key."""
        keys = [s["key"] for s in OPTIMIZED_SECTIONS]
        price_key = next(
            (s["key"] for s in OPTIMIZED_SECTIONS if "分项报价表" in s["name"]),
            None,
        )
        assert price_key is not None, "No 分项报价表 section key found"
        assert price_key in OPTIMIZED_SECTION_PROMPTS, (
            f"No prompt defined for {price_key}"
        )

    def test_optimized_sections_count(self):
        """OPTIMIZED_SECTIONS should have 9 sections (was 8, now +1 for 分项报价表)."""
        assert len(OPTIMIZED_SECTIONS) == 9, (
            f"Expected 9 sections, got {len(OPTIMIZED_SECTIONS)}: "
            f"{[s['name'] for s in OPTIMIZED_SECTIONS]}"
        )

    def test_section_numbering_sequential(self):
        """Section names should be sequentially numbered 一 through 九."""
        expected_numbers = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
        actual_numbers = [s["name"].split("、")[0] for s in OPTIMIZED_SECTIONS]
        assert actual_numbers == expected_numbers, (
            f"Section numbering mismatch: expected {expected_numbers}, "
            f"got {actual_numbers}"
        )

    def test_price_table_position(self):
        """分项报价表 should be at position 6 (after 偏差表, before 资格审查)."""
        names = [s["name"] for s in OPTIMIZED_SECTIONS]
        price_idx = next(
            i for i, n in enumerate(names) if "分项报价表" in n
        )
        deviation_idx = next(
            i for i, n in enumerate(names) if "偏差表" in n
        )
        qualification_idx = next(
            i for i, n in enumerate(names) if "资格审查" in n
        )
        assert deviation_idx < price_idx < qualification_idx, (
            f"分项报价表 should be between 偏差表 and 资格审查: "
            f"deviation={deviation_idx}, price={price_idx}, "
            f"qualification={qualification_idx}"
        )


# ── Cross-reference checker: document composition ──────────────────────


class TestDocumentCompositionConsistency:
    """Tests for _check_document_composition_consistency."""

    def test_missing_price_table_detected(self):
        """When bid letter declares 分项报价表 but no section exists, flag it."""
        sections = {
            "一、投标函": (
                "致招标人：\n"
                "我方的投标文件包括下列内容：\n"
                "（1）投标函；\n"
                "（2）身份证明；\n"
                "（3）保证金；\n"
                "（4）偏差表；\n"
                "（5）分项报价表；\n"
                "（6）资格审查资料；\n"
                "（7）服务方案；\n"
                "（8）应急保障方案。\n"
            ),
            "二、法定代表人身份证明": "身份证明内容",
            "三、授权委托书": "授权委托书内容",
            "四、投标保证金": "保证金内容",
            "五、商务和技术偏差表": "偏差表内容",
            # NOTE: 六、分项报价表 is MISSING — this is the bug
            "六、资格审查资料": "资格审查内容",
            "七、服务方案": "服务方案内容",
            "八、应急保障方案": "应急保障方案内容",
        }

        issues = _check_document_composition_consistency(sections)
        assert len(issues) == 1, f"Expected 1 issue, got {issues}"
        assert issues[0]["type"] == "document_composition_mismatch"
        assert "分项报价表" in issues[0]["detail"]
        assert issues[0]["severity"] == "high"

    def test_all_declared_items_present_passes(self):
        """When all declared items have corresponding sections, no issues."""
        sections = {
            "一、投标函": (
                "致招标人：\n"
                "我方的投标文件包括下列内容：\n"
                "投标函、身份证明、授权委托书、保证金、偏差表、"
                "分项报价表、资格审查资料、服务方案、应急保障方案。\n"
            ),
            "二、法定代表人身份证明": "身份证明内容",
            "三、授权委托书": "授权委托书内容",
            "四、投标保证金": "保证金内容",
            "五、商务和技术偏差表": "偏差表内容",
            "六、分项报价表": "分项报价表内容",
            "七、资格审查资料": "资格审查内容",
            "八、服务方案": "服务方案内容",
            "九、应急保障方案": "应急保障方案内容",
        }

        issues = _check_document_composition_consistency(sections)
        assert len(issues) == 0, f"Expected no issues, got: {issues}"

    def test_no_bid_letter_no_issue(self):
        """When there's no bid letter section, no composition check is done."""
        sections = {
            "服务方案": "服务方案内容",
        }
        issues = _check_document_composition_consistency(sections)
        assert len(issues) == 0

    def test_no_composition_list_no_issue(self):
        """When bid letter has no composition keywords, no check is done."""
        sections = {
            "一、投标函": "这是一封简单的投标函，没有列出文件组成清单。",
        }
        issues = _check_document_composition_consistency(sections)
        assert len(issues) == 0

    def test_multiple_missing_items_detected(self):
        """When multiple declared items are missing, all should be flagged."""
        sections = {
            "一、投标函": (
                "投标文件组成：投标函、身份证明、保证金、偏差表、"
                "分项报价表、资格审查资料、服务方案、应急保障方案。"
            ),
            "二、法定代表人身份证明": "内容",
            # Missing: 保证金, 偏差表, 分项报价表, 资格审查, 服务方案, 应急保障
        }

        issues = _check_document_composition_consistency(sections)
        assert len(issues) == 1
        missing = issues[0]["declared_items"]
        assert "保证金" in missing
        assert "偏差表" in missing
        assert "分项报价表" in missing
        assert "资格审查" in missing
        assert "服务方案" in missing
        assert "应急保障" in missing


# ── Integration: cross_reference_checker node ──────────────────────────


class TestCrossReferenceNodeWithComposition:
    """Integration tests for the full cross_reference_checker node."""

    def test_node_flags_missing_price_table(self):
        """The cross_reference_checker node should FAIL when 分项报价表 is
        declared in the bid letter but no section exists for it."""
        sections = {
            "一、投标函": (
                "投标文件组成清单：投标函、身份证明、授权委托书、保证金、"
                "偏差表、分项报价表、资格审查资料、服务方案、应急保障方案。"
            ),
            "二、法定代表人身份证明": "内容",
            "三、授权委托书": "内容",
            "四、投标保证金": "内容",
            "五、商务和技术偏差表": "内容",
            # 六、分项报价表 MISSING
            "六、资格审查资料": "内容",
            "七、服务方案": "内容",
            "八、应急保障方案": "内容",
        }

        state = factory_state(sections=sections)
        result = cross_reference_checker(state)
        report = result["cross_ref_report"]

        assert report["verdict"] == "FAIL", (
            f"Expected FAIL due to missing 分项报价表, got {report['verdict']}: "
            f"{report['inconsistencies']}"
        )

        composition_issues = [
            i for i in report["inconsistencies"]
            if i.get("type") == "document_composition_mismatch"
        ]
        assert len(composition_issues) == 1
        assert "分项报价表" in composition_issues[0]["detail"]

    def test_node_passes_with_all_sections(self):
        """The cross_reference_checker node should PASS when all declared
        items (including 分项报价表) have corresponding sections."""
        sections = {
            "一、投标函": (
                "投标文件组成清单：投标函、身份证明、授权委托书、保证金、"
                "偏差表、分项报价表、资格审查资料、服务方案、应急保障方案。"
            ),
            "二、法定代表人身份证明": "内容",
            "三、授权委托书": "内容",
            "四、投标保证金": "内容",
            "五、商务和技术偏差表": "内容",
            "六、分项报价表": "分项报价表内容",
            "七、资格审查资料": "内容",
            "八、服务方案": "内容",
            "九、应急保障方案": "内容",
        }

        state = factory_state(sections=sections)
        result = cross_reference_checker(state)
        report = result["cross_ref_report"]

        # Should not have composition mismatch issues
        composition_issues = [
            i for i in report["inconsistencies"]
            if i.get("type") == "document_composition_mismatch"
        ]
        assert len(composition_issues) == 0, (
            f"Should have no composition issues: {composition_issues}"
        )


# ── Prompt content tests ────────────────────────────────────────────────


class TestPriceTablePrompt:
    """Verify the 分项报价表 prompt content."""

    def test_prompt_mentions_tender_requirement(self):
        """The prompt should reference 招标文件 3.1.1 requirement."""
        from core.nodes.optimized_prompts import get_optimized_section_prompt
        prompt = get_optimized_section_prompt("sec5b_price_table", "test")
        assert "3.1.1" in prompt or "招标文件" in prompt

    def test_prompt_requires_consistency_with_bid_letter(self):
        """The prompt should require the total to match the bid letter price."""
        from core.nodes.optimized_prompts import get_optimized_section_prompt
        prompt = get_optimized_section_prompt("sec5b_price_table", "test")
        assert "投标函" in prompt
        assert "一致" in prompt

    def test_prompt_uses_placeholders(self):
        """The prompt should use 【待填写】 placeholders for prices."""
        from core.nodes.optimized_prompts import get_optimized_section_prompt
        prompt = get_optimized_section_prompt("sec5b_price_table", "test")
        assert "【待填写" in prompt


if __name__ == "__main__":
    print("=== Test: OPTIMIZED_SECTIONS includes price table ===")
    t = TestOptimizedSectionsIncludePriceTable()
    t.test_price_table_section_exists()
    print("  PASSED")
    t.test_price_table_section_key_exists()
    print("  PASSED")
    t.test_optimized_sections_count()
    print("  PASSED")
    t.test_section_numbering_sequential()
    print("  PASSED")
    t.test_price_table_position()
    print("  PASSED")

    print("\n=== Test: Missing price table detected ===")
    t2 = TestDocumentCompositionConsistency()
    t2.test_missing_price_table_detected()
    print("  PASSED")

    print("\n=== Test: All items present passes ===")
    t2.test_all_declared_items_present_passes()
    print("  PASSED")

    print("\n=== Test: No bid letter ===")
    t2.test_no_bid_letter_no_issue()
    print("  PASSED")

    print("\n=== Test: No composition list ===")
    t2.test_no_composition_list_no_issue()
    print("  PASSED")

    print("\n=== Test: Multiple missing items ===")
    t2.test_multiple_missing_items_detected()
    print("  PASSED")

    print("\n=== Test: Node flags missing price table ===")
    t3 = TestCrossReferenceNodeWithComposition()
    t3.test_node_flags_missing_price_table()
    print("  PASSED")

    print("\n=== Test: Node passes with all sections ===")
    t3.test_node_passes_with_all_sections()
    print("  PASSED")

    print("\n=== Test: Prompt content ===")
    t4 = TestPriceTablePrompt()
    t4.test_prompt_mentions_tender_requirement()
    print("  PASSED")
    t4.test_prompt_requires_consistency_with_bid_letter()
    print("  PASSED")
    t4.test_prompt_uses_placeholders()
    print("  PASSED")

    print("\n✅ All tests passed!")
