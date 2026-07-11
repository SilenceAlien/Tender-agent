"""Integration tests for Mermaid diagram support in section generation and DOCX export.

Verifies:
- _mermaid_to_text_diagram parses Mermaid flowchart syntax correctly
- DOCX export converts Mermaid blocks to text tree diagrams
- Section prompts include diagram instructions for relevant chapters
- Optimized prompts (劳务外包类) include Mermaid examples
"""

import re
import tempfile
from pathlib import Path

import pytest

from core.nodes.doc_assembler import (
    _build_docx,
    _mermaid_to_text_diagram,
    DEFAULT_FORMAT,
)
from core.nodes.section_generator import (
    _SECTION_PROMPTS,
    _DIAGRAM_INSTRUCTION,
    _get_section_prompt,
    _DIAGRAM_SECTIONS,
)
from core.nodes.optimized_prompts import (
    OPTIMIZED_SECTION_PROMPTS,
    get_optimized_section_prompt,
)


# ── Mermaid Parser Tests ───────────────────────────────────────────────


class TestMermaidToTextDiagram:
    """Tests for _mermaid_to_text_diagram converter."""

    def test_simple_flowchart_td(self):
        """Top-down org chart with 1 root and 3 children."""
        mermaid_code = """
flowchart TD
    A[项目负责人] --> B[入职资料组]
    A --> C[薪酬社保组]
    A --> D[培训管理组]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        # Should produce at least 4 lines: root + 3 children
        assert len(lines) >= 4
        # Root should contain the root label
        assert any("项目负责人" in line for line in lines)
        # All children should be present
        assert any("入职资料组" in line for line in lines)
        assert any("薪酬社保组" in line for line in lines)
        assert any("培训管理组" in line for line in lines)

    def test_flowchart_lr(self):
        """Left-to-right process flow."""
        mermaid_code = """
flowchart LR
    A[需求确认] --> B[人员招聘]
    B --> C[背景核查]
    C --> D[签订合同]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert len(lines) >= 4
        # Verify sequential order is preserved
        joined = "\n".join(lines)
        assert joined.index("需求确认") < joined.index("人员招聘")
        assert joined.index("人员招聘") < joined.index("背景核查")
        assert joined.index("背景核查") < joined.index("签订合同")

    def test_multi_level_hierarchy(self):
        """Multi-level org chart (3 levels deep)."""
        mermaid_code = """
flowchart TD
    A[总经理] --> B[项目部]
    B --> C[项目经理1]
    B --> D[项目经理2]
    A --> E[财务部]
    E --> F[会计]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert len(lines) >= 6
        assert any("总经理" in line for line in lines)
        assert any("项目部" in line for line in lines)
        assert any("财务部" in line for line in lines)
        assert any("会计" in line for line in lines)

    def test_edge_with_label(self):
        """Edges with |label| syntax."""
        mermaid_code = """
flowchart LR
    A[开始] -->|审核通过| B[执行]
    A -->|审核拒绝| C[退回]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert any("审核通过" in line for line in lines)
        assert any("审核拒绝" in line for line in lines)

    def test_parenthesis_nodes(self):
        """Nodes defined with parentheses A(Label)."""
        mermaid_code = """
flowchart TD
    A(开始) --> B(处理)
    B --> C(结束)
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert any("开始" in line for line in lines)
        assert any("处理" in line for line in lines)
        assert any("结束" in line for line in lines)

    def test_brace_nodes(self):
        """Nodes defined with braces A{Label} (decision nodes)."""
        mermaid_code = """
flowchart TD
    A[请求] --> B{是否通过?}
    B --> C[批准]
    B --> D[拒绝]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert any("是否通过" in line for line in lines)
        assert any("批准" in line for line in lines)
        assert any("拒绝" in line for line in lines)

    def test_comments_ignored(self):
        """Mermaid comments (%%) should be ignored."""
        mermaid_code = """
flowchart TD
    %% This is a comment
    A[Root] --> B[Child]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert not any("comment" in line.lower() for line in lines)
        assert any("Root" in line for line in lines)

    def test_empty_code(self):
        """Empty mermaid code should not crash."""
        lines = _mermaid_to_text_diagram("")
        assert isinstance(lines, list)

    def test_no_edges_fallback(self):
        """Mermaid code with no parseable edges falls back to raw output."""
        mermaid_code = """
flowchart TD
    A[Node A]
    B[Node B]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        # Should return fallback with raw code
        assert isinstance(lines, list)
        assert len(lines) >= 1

    def test_cyclic_graph(self):
        """Cyclic graph should not cause infinite loop."""
        mermaid_code = """
flowchart LR
    A[Start] --> B[Process]
    B --> A[Start]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        assert isinstance(lines, list)
        assert len(lines) >= 1

    def test_tree_indentation(self):
        """Output should use tree characters (├──, └──, │)."""
        mermaid_code = """
flowchart TD
    A[Root] --> B[Child1]
    A --> C[Child2]
"""
        lines = _mermaid_to_text_diagram(mermaid_code)
        joined = "\n".join(lines)
        # Should contain tree drawing characters
        assert "──" in joined


# ── DOCX Export with Mermaid Tests ─────────────────────────────────────


class TestDocxWithMermaid:
    """Tests for DOCX export with Mermaid diagram content."""

    def test_mermaid_in_docx_produces_image(self):
        """Section content with Mermaid block should produce an image in DOCX."""
        sections = {
            "第五章 人员配置方案": (
                "本项目团队组织架构如下：\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    A[项目负责人] --> B[入职资料组]\n"
                "    A --> C[薪酬社保组]\n"
                "    A --> D[培训管理组]\n"
                "```\n"
                "以上为团队组织架构图。"
            ),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = str(Path(tmpdir) / "test_mermaid.docx")
            _build_docx(sections, DEFAULT_FORMAT, export_path)

            from docx import Document
            doc = Document(export_path)
            all_text = "\n".join(p.text for p in doc.paragraphs)

            # Should not contain raw mermaid code block markers
            assert "```mermaid" not in all_text
            # Surrounding text should still be present
            assert "团队组织架构" in all_text
            assert "以上为团队组织架构图" in all_text
            # Should have at least one embedded image (the rendered diagram)
            assert len(doc.inline_shapes) >= 1, (
                f"Expected ≥1 inline image, got {len(doc.inline_shapes)}"
            )

    def test_mermaid_and_placeholder_coexist(self):
        """Mermaid diagrams (as images) and red-bold placeholders should coexist."""
        sections = {
            "服务方案": (
                "下图展示了团队架构：\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    A[项目经理] --> B[技术组]\n"
                "    A --> C[运营组]\n"
                "```\n"
                "项目负责人：【待填写：项目经理姓名】\n"
                "联系电话：【待填写：联系电话】"
            ),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = str(Path(tmpdir) / "test_combined.docx")
            _build_docx(sections, DEFAULT_FORMAT, export_path)

            from docx import Document
            from docx.shared import RGBColor
            doc = Document(export_path)

            # Should have an embedded diagram image
            assert len(doc.inline_shapes) >= 1

            # Should have red bold placeholders
            placeholder_runs = [
                run for para in doc.paragraphs for run in para.runs
                if "待填写" in run.text
            ]
            assert len(placeholder_runs) >= 2
            for run in placeholder_runs:
                assert run.bold is True
                assert run.font.color.rgb == RGBColor(0xFF, 0x00, 0x00)

    def test_multiple_mermaid_blocks(self):
        """Multiple Mermaid blocks in one section should all be rendered as images."""
        sections = {
            "服务方案": (
                "团队架构：\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    A[负责人] --> B[组1]\n"
                "    A --> C[组2]\n"
                "```\n"
                "入职流程：\n"
                "```mermaid\n"
                "flowchart LR\n"
                "    D[招聘] --> E[面试]\n"
                "    E --> F[入职]\n"
                "```\n"
            ),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = str(Path(tmpdir) / "test_multi_mermaid.docx")
            _build_docx(sections, DEFAULT_FORMAT, export_path)

            from docx import Document
            doc = Document(export_path)
            all_text = "\n".join(p.text for p in doc.paragraphs)

            # Should have 2 embedded images (one per mermaid block)
            assert len(doc.inline_shapes) >= 2, (
                f"Expected ≥2 inline images, got {len(doc.inline_shapes)}"
            )
            # No raw mermaid markers in text
            assert "```mermaid" not in all_text
            # Surrounding text should still be present
            assert "团队架构" in all_text
            assert "入职流程" in all_text

    def test_section_without_mermaid_still_works(self):
        """Sections without Mermaid blocks should work as before."""
        sections = {
            "投标函": "致招标人：\n我方郑重承诺按照招标文件要求提供服务。",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = str(Path(tmpdir) / "test_no_mermaid.docx")
            _build_docx(sections, DEFAULT_FORMAT, export_path)

            from docx import Document
            doc = Document(export_path)
            all_text = "\n".join(p.text for p in doc.paragraphs)
            assert "致招标人" in all_text
            assert "郑重承诺" in all_text


# ── Prompt Injection Tests ─────────────────────────────────────────────


class TestDiagramPromptInjection:
    """Tests for diagram instruction injection in section prompts."""

    def test_diagram_instruction_exists(self):
        """_DIAGRAM_INSTRUCTION should be defined and non-empty."""
        assert _DIAGRAM_INSTRUCTION
        assert "mermaid" in _DIAGRAM_INSTRUCTION.lower()
        assert "flowchart" in _DIAGRAM_INSTRUCTION.lower()

    def test_diagram_sections_set(self):
        """_DIAGRAM_SECTIONS should include personnel and workflow chapters."""
        assert "ch3_service" in _DIAGRAM_SECTIONS
        assert "ch5_staffing" in _DIAGRAM_SECTIONS
        assert "ch7_schedule" in _DIAGRAM_SECTIONS

    def test_ch5_staffing_prompt_mentions_mermaid(self):
        """ch5_staffing prompt should mention Mermaid flowchart."""
        prompt = _SECTION_PROMPTS["ch5_staffing"]
        assert "mermaid" in prompt.lower() or "Mermaid" in prompt
        assert "flowchart" in prompt

    def test_ch3_service_prompt_mentions_mermaid(self):
        """ch3_service prompt should mention Mermaid."""
        prompt = _SECTION_PROMPTS["ch3_service"]
        assert "mermaid" in prompt.lower() or "Mermaid" in prompt

    def test_ch7_schedule_prompt_mentions_mermaid(self):
        """ch7_schedule prompt should mention Mermaid."""
        prompt = _SECTION_PROMPTS["ch7_schedule"]
        assert "mermaid" in prompt.lower() or "Mermaid" in prompt

    def test_diagram_instruction_injected_for_staffing(self):
        """_get_section_prompt should inject _DIAGRAM_INSTRUCTION for ch5_staffing."""
        prompt = _get_section_prompt("ch5_staffing", "无特定要求", bid_type="")
        assert _DIAGRAM_INSTRUCTION.strip() in prompt

    def test_diagram_instruction_injected_for_service(self):
        """_get_section_prompt should inject _DIAGRAM_INSTRUCTION for ch3_service."""
        prompt = _get_section_prompt("ch3_service", "无特定要求", bid_type="")
        assert _DIAGRAM_INSTRUCTION.strip() in prompt

    def test_diagram_instruction_not_injected_for_letter(self):
        """_get_section_prompt should NOT inject _DIAGRAM_INSTRUCTION for ch1_letter."""
        prompt = _get_section_prompt("ch1_letter", "无特定要求", bid_type="")
        assert _DIAGRAM_INSTRUCTION.strip() not in prompt

    def test_diagram_instruction_contains_examples(self):
        """_DIAGRAM_INSTRUCTION should contain usage examples."""
        assert "flowchart TD" in _DIAGRAM_INSTRUCTION
        assert "flowchart LR" in _DIAGRAM_INSTRUCTION

    def test_optimized_sec7_has_mermaid(self):
        """Optimized sec7_service_plan prompt should mention Mermaid for team architecture."""
        prompt = get_optimized_section_prompt("sec7_service_plan", "无特定要求")
        assert "mermaid" in prompt.lower() or "Mermaid" in prompt
        assert "flowchart TD" in prompt

    def test_optimized_sec7_has_example_diagram(self):
        """Optimized sec7_service_plan should contain a Mermaid example."""
        prompt = OPTIMIZED_SECTION_PROMPTS["sec7_service_plan"]
        assert "```mermaid" in prompt
        assert "flowchart TD" in prompt

    def test_optimized_sec7_workflow_mentions_mermaid(self):
        """Optimized sec7 should mention Mermaid for workflow sections."""
        prompt = OPTIMIZED_SECTION_PROMPTS["sec7_service_plan"]
        assert "Mermaid flowchart LR" in prompt

    def test_optimized_sec7_format_succeeds(self):
        """Optimized sec7 prompt should format without errors."""
        prompt = get_optimized_section_prompt("sec7_service_plan", "评分标准测试")
        assert "评分标准测试" in prompt
        assert "mermaid" in prompt.lower()
