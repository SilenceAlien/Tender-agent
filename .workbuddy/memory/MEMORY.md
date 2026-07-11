# 标书项目 — 共享上下文（精简索引）

<!--
  设计原则：本文件控制在 ~500 tokens 以内。
  只存「每次对话都需要的核心信息」，详细资料按需加载。
  超过 500 token 的内容自动拆分到 reference/ 子目录。

  每个 Skill 和 Expert 读取本文件后，根据「索引」按需加载对应的详细资料。
-->

## 当前项目（~100 tokens）
- 项目名称: [待填写]
- 客户单位: [待填写]
- 招标编号: [待填写]
- 投标类型: 政府采购
- 截止日期: [待填写]
- 当前阶段: 准备阶段

## 核心策略（~150 tokens）

### 差异化优势（一句话）
[待填写，例如：10年行业经验 + 自研平台 + 本地化服务]

### 报价基调
[待填写，例如：中等偏上报价 + 增值服务包]

### 风格基调
专业严谨，数据驱动，避免空话套话。禁用绝对化用语。

## 标书制作智能体方案（2026-06-28）
- 技术栈：LangGraph + LangChain + Streamlit + Chroma/FAISS
- 7 节点状态机：DocParser → ReqExtractor → TemplateMatcher → SectionGenerator ↔ QualityChecker/FeedbackProc → Assembler
- 多模型适配：ChatOpenAI / Anthropic / 智谱 / 通义千问 / DeepSeek / Moonshot，API Key 从 .env 或 GUI 配置
- GUI：Streamlit 四面板（配置/上传/审阅/导出），人工在环微调循环

## 近期关键决策（~100 tokens）
<!-- 只保留最近 3-5 条，旧决策归档到 reference/decisions.md -->
- 项目级规范：已配置 AGENTS.md（Spec Kit + Superpowers + Planning with Files 三合一），含 TDD 红绿重构、双阶段 Review、原子任务分解、Worktree 隔离、自进化机制等
- 标书智能体：采用 LangGraph StateGraph 编排，QualityChecker 条件路由驱动微调循环
- 前端选 Streamlit 快速原型，Python 同语言零摩擦

## 详细资料索引（按需加载）
<!-- 本表只记录路径和预估 tokens，实际内容不在此文件中 -->

| 需要时加载 | 文件路径 | 预估 tokens |
|-----------|---------|-------------|
| 完整术语表 | reference/术语表.md | ~200 |
| 评分标准详情 | reference/评分标准.md | ~800 |
| 历史决策记录 | reference/decisions.md | ~1000 |
| 客户背景分析 | reference/客户分析.md | ~500 |
| 竞争对手情报 | reference/竞品分析.md | ~600 |
| 标书类型模板 | prompts/标书类型/ (6种: 服务/货物/软件/工程/集成/运维) | 各 ~750 |

## 当前活跃章节
<!-- 标注当前正在撰写/审查的章节，帮助快速定位上下文 -->
- [待填写]
