"""R1 三层反馈消费侧注入测试（specs/010-dead-code-cleanup 批次 1，断裂点 A 修复）。

背景：FeedbackProcessor（M4）正确回写三层结构到 state——
  Layer 1 ``global_constraints``（跨章节全局硬性约束，永不丢弃）
  Layer 2 ``compressed_history``（去重压缩的历史反馈摘要字符串）
  Layer 3 ``context_window_size``（近 N 轮上下文窗口，默认 3）
但 SectionGenerator 此前只消费扁平 ``feedback_history``，三层零消费（grep 证实
全文件无 global_constraints / compressed_history / get_recent_context_window 引用），
导致 PRD §4.1.5「定向修订不丢历史约束」打折——多轮微调时全局硬性约束与压缩历史
未注入生成 prompt。

本测试验证并行生成路径（graph.py:56 生产接线 generate_all_sections_parallel）：
  1. Layer 1 全局约束块注入所有生成 prompt（置顶，含「全局硬性约束」标记）
  2. Layer 2 压缩历史摘要块注入（限长 ~500 字，超长截断）
  3. Layer 3 定向修订仅取最近 N 轮反馈（窗口外的旧轮次不进入修订指令）
  4. 干净 state（首轮生成）零注入，行为与修复前完全一致
"""

from __future__ import annotations

from core.nodes.section_generator import generate_all_sections_parallel
from core.state import factory_state

# ── Helpers ────────────────────────────────────────────────────────────


def _fb_record(
    round_no: int,
    text: str,
    target: str = "",
    scope: str = "local",
    ftype: str = "content_fix",
) -> dict:
    """构造一条与 FeedbackProcessor 写侧结构一致的反馈记录。"""
    return {
        "round": round_no,
        "feedback_text": text,
        "feedback_type": ftype,
        "scope": scope,
        "target_section": target,
        "target_paragraph_index": -1,
        "diff_before": "",
        "diff_after": "",
        "timestamp": f"2026-09-06T00:00:{round_no:02d}+00:00",
    }


class PromptRecorder:
    """记录每次 LLM 调用 prompt 的假 llm_fn。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"[mock] 已按提示词生成 {len(prompt)} 字内容"

    def prompts_with(self, needle: str) -> list[str]:
        return [p for p in self.prompts if needle in p]


SECTION_SERVICE = "第三章 服务方案"
SECTION_LETTER = "第一章 投标函"

L1_HEADER = "全局硬性约束"
L2_HEADER = "历史反馈摘要"


# ── Layer 1: 全局约束注入 ──────────────────────────────────────────────


class TestLayer1GlobalConstraints:
    def test_constraint_injected_into_all_generated_prompts(self):
        """全局硬性约束应注入所有生成中的章节 prompt（scope=global 跨章节语义）。"""
        constraint = "报价单位必须为万元，保留两位小数"
        state = factory_state(
            global_constraints=[constraint],
        )
        recorder = PromptRecorder()
        result = generate_all_sections_parallel(state, llm_fn=recorder)

        assert result["sections"], "应生成全部章节"
        assert recorder.prompts, "应发生 LLM 调用"
        # 每一个生成 prompt 都必须携带该约束及其块标记
        assert len(recorder.prompts_with(constraint)) == len(recorder.prompts)
        assert len(recorder.prompts_with(L1_HEADER)) == len(recorder.prompts)

    def test_multiple_constraints_all_injected(self):
        """多条全局约束逐条列出，不丢项。"""
        constraints = [
            "正文中不得出现「我司拥有绝对优势」等绝对化用语",
            "服务期限统一表述为「12 个月」",
            "金额大小写必须一致",
        ]
        state = factory_state(global_constraints=constraints)
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        for c in constraints:
            assert recorder.prompts_with(c), f"约束未注入 prompt：{c}"

    def test_constraint_block_positioned_before_section_prompt(self):
        """Layer 1 是不可变硬约束，注入块应位于 prompt 顶部（置于章节正文提示之前）。"""
        state = factory_state(
            global_constraints=["工期表述必须与招标文件一致"],
        )
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        prompt = recorder.prompts[0]
        constraint_pos = prompt.find("工期表述必须与招标文件一致")
        # 章节提示主体（以「投标函」等章节名标记）应在约束块之后
        body_pos = min(
            (prompt.find(name) for name in ("投标函", "服务方案") if prompt.find(name) >= 0),
            default=len(prompt),
        )
        assert constraint_pos >= 0
        assert constraint_pos < body_pos, "全局约束块应置于 prompt 顶部"


# ── Layer 2: 压缩历史摘要注入 ──────────────────────────────────────────


class TestLayer2CompressedHistory:
    def test_compressed_history_injected(self):
        """state.compressed_history（FeedbackProcessor 写侧产物）应被读侧消费。"""
        summary = "[R1] (local/content_fix@第三章 服务方案) 报价金额前后不一致"
        state = factory_state(compressed_history=summary)
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        assert recorder.prompts_with(summary), "压缩历史摘要未注入 prompt"
        assert recorder.prompts_with(L2_HEADER)

    def test_compressed_history_truncated_to_500_chars(self):
        """超长压缩历史截断至 ~500 字，避免 prompt 膨胀（方案 R1 要求限长）。"""
        tail_marker = "这是第1000字附近的尾部内容不应出现"
        long_summary = ("历史问题摘要：" + "报价与工期表述不一致，需逐项核对。" * 100) + tail_marker
        assert len(long_summary) > 600
        state = factory_state(compressed_history=long_summary)
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        prompt = recorder.prompts[0]
        # 头部 500 字应保留
        assert long_summary[:500] in prompt
        # 尾部（1000+ 字处）应被截断
        assert tail_marker not in prompt


# ── Layer 3: 近 N 轮窗口 ───────────────────────────────────────────────


class TestLayer3RecentWindow:
    def test_revision_only_contains_recent_rounds(self):
        """定向修订指令仅含最近 context_window_size 轮反馈，窗口外旧轮次不进入。"""
        old_text = "第1轮的旧反馈：目录层级需要调整"  # round 1，窗口外
        recent_text = "第5轮的新反馈：服务方案缺少应急保障小节"  # round 5，窗口内
        state = factory_state(
            sections={SECTION_SERVICE: "旧版服务方案内容"},
            feedback_history=[
                _fb_record(1, old_text, target=SECTION_SERVICE),
                _fb_record(2, "第2轮反馈：格式问题", target=SECTION_SERVICE),
                _fb_record(3, "第3轮反馈：字数不足", target=SECTION_SERVICE),
                _fb_record(4, "第4轮反馈：案例陈旧", target=SECTION_SERVICE),
                _fb_record(5, recent_text, target=SECTION_SERVICE),
            ],
            context_window_size=3,
        )
        recorder = PromptRecorder()
        result = generate_all_sections_parallel(state, llm_fn=recorder)

        # 服务方案因有反馈被强制再生成，其 prompt 应含窗口内最新反馈
        assert recorder.prompts_with(recent_text), "最近轮反馈未进入修订指令"
        # 窗口外（round 1）旧反馈不应出现在任何修订指令中
        assert not recorder.prompts_with(old_text), "窗口外旧反馈不应进入修订指令"
        # 服务方案确实被再生成
        assert result["sections"][SECTION_SERVICE] != "旧版服务方案内容"

    def test_default_window_size_3_from_state(self):
        """窗口大小取 state.context_window_size（写侧维护的字段），而非硬编码。"""
        texts = [f"第{r}轮反馈：问题{r}" for r in range(1, 8)]  # rounds 1..7
        state = factory_state(
            sections={SECTION_LETTER: "旧版投标函"},
            feedback_history=[
                _fb_record(r, t, target=SECTION_LETTER) for r, t in zip(range(1, 8), texts)
            ],
            context_window_size=2,  # 显式设为 2：仅 round 6,7 进入窗口
        )
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        assert recorder.prompts_with("第7轮反馈：问题7")
        assert recorder.prompts_with("第6轮反馈：问题6")
        assert not recorder.prompts_with("第5轮反馈：问题5"), "窗口=2 时 round5 应在窗口外"
        assert not recorder.prompts_with("第1轮反馈：问题1")


# ── 零行为变更守卫 ─────────────────────────────────────────────────────


class TestCleanStateNoInjection:
    def test_first_pass_prompts_unchanged(self):
        """干净 state（首轮，三层全空）不应出现任何注入块标记，行为与修复前一致。"""
        state = factory_state()
        recorder = PromptRecorder()
        generate_all_sections_parallel(state, llm_fn=recorder)

        assert recorder.prompts
        for p in recorder.prompts:
            assert L1_HEADER not in p
            assert L2_HEADER not in p
