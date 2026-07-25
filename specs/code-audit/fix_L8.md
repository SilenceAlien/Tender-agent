# Fix 记录 — L8（低危）：database.py 未接入运行时

**审计来源**：`specs/code-audit/cluster_C8_v2.md` §1 表格 L8 行
**评估对象**：`bid-agent/core/database.py`
**评估日期**：2026-07-20
**处理结论**：**未改动代码逻辑，仅补充模块 docstring 文档说明**

---

## 1. 评估结论

`core/database.py` 实现了一套完整的 SQLite CRUD（projects / documents / sections / feedbacks / configs），API 本身具备「跨会话保存 pipeline 运行记录、企业资质库缓存」的潜在价值。但经完整阅读 + 全仓 grep 确认：

- `from core.database` **仅命中** `tests/unit/test_database.py:9`，运行时未被引用。
- 运行时持久化实际分两处：
  - 用户配置 / API Key → `core/config_persistence.py`（文件 JSON，位于 `~/.bid-agent/config.json`，安全模型在家目录）。
  - metrics / prompt 演化 → 各自独立 `_init_db()`（`core/evolution/metrics_tracker.py:67`、`prompt_registry.py:65`），**不引用**本模块。
- 运行时 pipeline 在内存 AgentState 上操作（§5 三模式均未提及 database.py），**没有「project」生命周期概念**来消费本模块的 projects/documents 等 API。

## 2. 是否改动代码

**未改动。** 仅在 `core/database.py` 顶部模块 docstring 补充了「运行时接入状态（L8）」说明，覆盖：
- 当前未接入、仅用于 tests 的事实；
- 运行时真实持久化方案指向 config_persistence.py；
- 建议接入方式（运行时入口以可选、失败不抛的方式写快照；或统一 metrics/prompt 的 `_init_db`）；
- 禁止事项（API Key 不得迁入本库、不得强制外部 DB 依赖、schema 幂等无需迁移）。

## 3. 未强行接入的原因

1. **无清晰、<20 行、低风险的单一接入点**：本模块 API 围绕「project」建模，而运行时无 project 概念，建立该生命周期属新功能，超出最小修复范围。
2. **config_persistence 回退到 database 有安全风险**：会把敏感 API Key 从家目录迁到 `data/bid_agent.db`，改变既有安全模型。
3. **运行时直接写 DB 失败会崩溃 pipeline**：GUI 主流程对 DB 异常需 try/except 隔离，属运行时风险，不宜在本次强加。
4. **存在已独立的 `_init_db()` 实现**：强行统一为共享后端是较大重构（>20 行），需单独评估。

## 4. 验证结果

```
/Users/alanchris/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
  -m py_compile bid-agent/core/database.py
→ py_compile OK（退出码 0）
```

docstring 仅为注释，不影响语法；编译通过。

## 5. 建议后续

- 若产品需要「跨会话运行记录 / 资质库缓存」，建议单独立项：在 `gui/pipeline_runner` 或 graph 完成回调中以「可选、失败不抛」方式调用 `create_project`/`save_agent_state`，并将 `metrics_tracker`/`prompt_registry` 的 `_init_db()` 收敛复用本模块。该工作量 >20 行，应作为独立任务评估，而非 L8 修复。
