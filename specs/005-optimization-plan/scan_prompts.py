"""用 prompt-guard 扫描优化后的 prompts，确保无注入风险。"""
import sys
sys.path.insert(0, "/Users/alanchris/.workbuddy/skills/prompt-guard")

from pathlib import Path
sys.path.insert(0, str(Path("/Users/alanchris/.workbuddy/skills/prompt-guard")))

from specs.occupation_plan_optimized_prompts import OPTIMIZED_SECTION_PROMPTS

try:
    from prompt_guard import PromptGuard
    guard = PromptGuard(config={"api": {"enabled": False}})

    print("=" * 60)
    print("Prompt-Guard 安全扫描结果")
    print("=" * 60)

    all_safe = True
    for key, prompt in OPTIMIZED_SECTION_PROMPTS.items():
        if key == "_fallback":
            continue
        result = guard.analyze(prompt)
        status = "SAFE" if result.action.value == "ALLOW" else f"{result.severity.value}/{result.action.value}"
        icon = "✅" if result.action.value == "ALLOW" else "🚨"
        print(f"\n{icon} [{key}] → {status}")
        if result.reasons:
            print(f"   原因: {result.reasons}")
            all_safe = False
        if result.patterns_matched:
            print(f"   匹配模式: {result.patterns_matched}")
            all_safe = False

    print("\n" + "=" * 60)
    if all_safe:
        print("✅ 全部 prompts 通过安全扫描，无注入风险")
    else:
        print("⚠️ 部分 prompts 触发安全规则，需检查")
    print("=" * 60)

except ImportError as e:
    print(f"prompt-guard 未安装或无法导入: {e}")
    print("跳过安全扫描（prompt-guard skill 目录下可能需要安装）")
