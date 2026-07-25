# 修复记录 — M16（中危）

> 对象：`bid-agent/core/llm/router.py`
> 问题：默认质检模型为 `openai/gpt-4o-mini`，与规范 §4.1「DeepSeek 为默认供应商」冲突，易误导维护者、且若某处依赖 router 默认会错用 OpenAI。
> 规范依据：`system-workflow.md:544`（DeepSeek `deepseek-chat` 为默认模型，用于生成/提取/质检）。
> 证据：`specs/code-audit/cluster_C8_v2.md` M16 段落。

## 修改内容

| 位置 | 旧值 | 新值 |
|------|------|------|
| `router.py:20-21`（`DEFAULT_NODE_MODELS["QualityChecker"]`） | `"QualityChecker": {"provider": "openai", "model": "gpt-4o-mini"},` | 新增注释 `# M16 fix: aligned with spec — default to DeepSeek, not openai/gpt-4o-mini`；`"QualityChecker": {"provider": "deepseek", "model": "deepseek-chat"},` |

其余节点（`ReqExtractor`/`SectionGenerator`/`FeedbackProcessor`）与全局默认 `DEFAULT_PROVIDER="deepseek"`、`DEFAULT_MODEL="deepseek-chat"`（`router.py:26-27`）本就为 DeepSeek，未改动。

## 符合性确认

1. **对齐规范**：默认供应商/模型已统一为 DeepSeek（`deepseek/deepseek-chat`），与 providers.py 中 `_create_deepseek` 工厂标识一致。
2. **缺省行为**：`get_llm()`（`router.py:83-84`）缺省回退到 `DEFAULT_PROVIDER`/`DEFAULT_MODEL`，改后任何未显式指定 provider/model 的节点均走 DeepSeek，符合规范。
3. **未强制 Key / 未改工厂**：未改动 providers.py 工厂，未要求 API Key 落地。
4. **与 pipeline_runner 无冲突**：`gui/pipeline_runner.py:95` 始终以显式 `provider=model=` 调用 `router.get_llm()`（来自 `config.json` 的 `get_model_for_node`），不依赖 router 默认常量，改动不影响运行时模型选择。

## 验证结果

- `py_compile core/llm/router.py` → **PY_COMPILE_OK（无语法错误）**。
- Grep 确认默认模型字符串已改为 DeepSeek 标识：`router.py:21` `QualityChecker` → `deepseek/deepseek-chat`；`router.py:26-27` 全局默认 `deepseek`/`deepseek-chat`。
- `git diff` 仅含上述 1 处 M16 相关改动，范围最小化。

## 结论

M16 已修复（工作区已落地，未提交）。默认质检模型 `openai/gpt-4o-mini` → `deepseek/deepseek-chat`，全局默认与全部节点默认一致为 DeepSeek，与规范 §4.1 对齐，验证通过。
