# 标书智能体全流程功能测试 — 完成概览

> 测试日期：2026-07-11
> 测试人：端测测（Web 应用测试专家）
> 范围：13 节点管道全部功能 + Streamlit UI + 多模型配置 + E2E
> 轮次：第一轮（N01-N14）→ 用户修复 → 第二轮回归（R01-R02）

## 执行摘要

对标书制作智能体（Bid Agent）进行了**两轮**全流程功能测试。

- **第一轮**：发现 14 个问题（N01-N14），记录在 bugs.md / bugs02.md
- **第二轮**（用户修复后回归）：验证 N01-N14 修复情况，**12/14 已确认修复**，新发现 2 个问题（R01-R02），记录在 bugs03.md

## 第二轮回归测试结果

| 维度 | 结果 |
|------|------|
| pytest | 507 通过 / 0 失败（17.9s） |
| Streamlit UI | HTTP 200，节点级模型配置 UI 已出现 |
| N01-N14 修复验证 | **12/14 已修复**，N02 仅文档化，N11 修复不彻底 |
| 新发现问题 | **2 个**（R01 高 / R02 中） |

## 新发现问题（2 个）

### R01 [高] config_provider 恒为 deepseek — 只配 OpenAI 的用户节点全部 fallback mock

N11 修复不彻底。`config_panel.py:128` 的 `"deepseek" if ds_model else ...` 中 `ds_model` 恒非空（selectbox 值），provider 永远是 deepseek。只配 OpenAI Key 的用户，未设 node_override 的节点全部 fallback 到 deepseek（无 key → mock），系统不可用。

### R02 [中] 交互模式 approved 路径无触发方式 — review_panel 缺 resume_after_review 调用

N02 仅文档化处理。graph.py 声明 `approved → DocumentAssembler` 路由，但 review_panel 没有调用 `resume_after_review()` 来触发该路径，approved 路由是死代码。导出仍靠 export_panel 按钮。

## 修复验证亮点（12 项已确认修复）

- **N01** export_path 已加入 AgentState，run_pipeline 返回非空路径 + 文件存在
- **N03** DS_MODELS 改为 deepseek-chat / deepseek-reasoner
- **N05** 契约禁止机构检测生效（"复旦大学" → CRITICAL）
- **N06** 技术参数矛盾检测生效（响应时间/到达现场）
- **N07** format_rules 缺字段 + 缺 seal_requirement 均报警
- **N08** 三处标书类型命名完全一致（7 种）
- **N09** 中文 2-gram 分词生效，"劳务管理服务方案" → 7 tokens，预测得分 24/40

## 交付物

| 文件 | 说明 |
|------|------|
| `bugs03.md` | 第二轮回归测试报告（修复验证表 + R01-R02 新问题 + 盲区分析） |
| `bugs02.md` | 第一轮 14 个问题（N01-N14）独立提取版 |
| `bugs.md` | 第一轮完整报告（旧 bug 核查 + N01-N14） |
| `/tmp/bid_smoke/regression_test.py` | 第二轮回归测试脚本 |

## 后续建议

1. **立即修复 R01**：config_provider 推导逻辑改为根据用户实际配置的 API Key 推导，或让用户显式选主用 provider
2. **处理 R02**：二选一——实现 review_panel 的 resume 逻辑，或移除 graph.py 的 approved 死路由
3. **补充测试覆盖**：交互模式 E2E、真实 OpenAI Key 验证 R01 连锁影响
4. **验证未覆盖旧 bug**：BUG-04/06/07/11（FAISS 持久化 + evolution 模块）
