# 标书制作智能体 — 产品优化方案

> 规范版本：v1.0
> 创建日期：2026-07-01
> 签名状态：待审阅

---

## 需求背景

当前标书 Agent 的管道架构（LangGraph 7 节点）是良好的骨架，但存在以下关键短板：

- **知识库数据为空**：`knowledge_base/` 下的"中标方案"文件绝大多数是 130 字节的占位符，注明"中标方案属于商业秘密，未公开"。TemplateMatcher 节点和 RAG 注入方案在当前条件下不可用。
- **Prompt 写死无适配**：SectionGenerator 的 `_SECTION_PROMPTS` 是静态模板，不根据标书类型（服务/货物/工程等）或评分标准动态调整。
- **质量检查粗糙**：LLM 检查仅截取每章前 2000 字，非 JSON 响应静默 PASS，只有单个 PASS/FAIL 判定无评分粒度。
- **无评分模拟**：生成的标书无法预估得分，用户不知道薄弱环节。
- **反馈循环弱**：发现问题后整章重写而非定向修订。

以上问题导致生成标书的可靠性和竞争力无法保障。

**产品成熟度评分：37 / 100**

---

## 阶段 0：构建模板知识库

**优先级：P0（必须先做）**
**预计耗时：3 天**

### 目标

从零构建可用的标书模板和章节范文库，为 RAG 注入和 FAISS 检索提供数据基础。

### 实现方案

使用 `scripts/build_knowledge_base.py` 脚本，以现有的 6 份招标文件 PDF 为输入源，通过 LLM 反向生成高分投标模板：

| 类型 | 源 PDF | 输出 |
|------|--------|------|
| 服务类 | 复旦大学物业_招标文件.pdf | 1 模板 + 8 范文 |
| 货物类 | 长春应化所_招标文件.pdf | 1 模板 + 8 范文 |
| 工程类 | 浙大紫金港景观_招标文件.pdf | 1 模板 + 8 范文 |
| 运维类 | 江苏税务局运维_招标文件.pdf | 1 模板 + 8 范文 |
| 集成类 | 中央民大数智门户_招标文件.pdf | 1 模板 + 8 范文 |
| 劳务外包类 | 厦门税务局食堂外包_招标文件.pdf | 1 模板 + 8 范文 |

### 留白规范

所有敏感数据使用 `【待填写：具体说明】` 格式留白：

```
- 个人身份证号、手机号、家庭住址
- 企业营业执照号、统一社会信用代码
- 财务数据（营业收入、利润、资产总额、纳税额）
- 认证证书编号（ISO 系列、行业许可等）
- 合同金额、项目金额
- 具体日期（合同签订日期、证书有效期等）
- 人名（法定代表人、项目成员、客户联系人等）
- 银行账号、保证金金额
- 政府部门名称（具体的区/县/市级别）
```

### 成功标准

- [x] 6 种标书类型各有 1 套完整 8 章模板和 8 篇章节范文
- [x] 模板中所有敏感数据已留白
- [ ] FAISS 索引可加载并检索模板内容
- [x] 脚本支持 `--type`、`--skip-existing`、`--essays-only` 等参数，方便增量构建

---

## 阶段 1：底座夯实

**优先级：P1**
**预计耗时：5 天**

### 1.1 知识库 RAG 注入 SectionGenerator

**当前状况**：`_SECTION_PROMPTS` 是写死的模板，不参考任何实际案例。

**改造方案**：在 `_get_section_prompt()` 中增加 RAG 检索步骤：

```
你是一位资深招投标专家。参考以下同类中标方案的写作风格和结构，
为本次招标撰写「{章节名}」：

【参考中标方案】
{rag_retrieved_winning_bids}

【本次招标评分要求】
{requirements_context}
```

**实现要点**：
- 用 FAISS 从 `knowledge_base/` 下的范文目录检索最相似的 3 份范文
- 按投标类型（服务/货物/工程等）预过滤，仅检索同类型
- 示例文本从 metadata.json 中的章节 key 匹配

**涉及文件**：`core/nodes/section_generator.py`、`core/retrieval/pipeline.py`

### 1.2 修复质量检查的三个致命漏洞

| 漏洞 | 位置 | 修复方案 |
|------|------|---------|
| 每章只查 2000 字 | `quality_checker.py:160` | 改为 `content[:8000]` 或分片逐段检查 |
| 非 JSON 静默 PASS | `quality_checker.py:183` | 重试 1 次，仍失败 → `verdict: "FAIL"` |
| 单维度 PASS/FAIL | 全局质量检查 | 改为 0-100 分 + 分类问题清单（完整度/合规度/一致度） |

**涉及文件**：`core/nodes/quality_checker.py`

### 1.3 标书类型贯穿全管道

**当前状况**：用户在 GUI 选了 "服务类" 但 `bid_type` 从未传给任何节点。

**改造方案**：

```python
# AgentState 增加 bid_type 字段
AgentState:
    bid_type: str  # "服务类" | "货物类" | ...

# 各节点消费方式
SectionGenerator:  根据 bid_type 选择 prompt 侧重点和范文来源
TemplateMatcher:   在对应类型的知识库子目录中搜索
QualityChecker:    根据类型有不同的评分标准权重
```

**涉及文件**：`core/state.py`、`core/nodes/section_generator.py`、`core/nodes/template_matcher.py`、`core/nodes/quality_checker.py`、`gui/panels/upload_panel.py`、`gui/panels/review_panel.py`

### 1.4 评分模拟器（新增节点）

在 QualityChecker → FeedbackProcessor / DocumentAssembler 之间插入 `ScoreSimulator`。

**输入**：`sections` + `requirements.scoring`
**输出**：

```json
{
  "scores": [
    {"item": "服务方案", "max_score": 30, "predicted": 28, "gap": 2, "suggestion": "缺少应急响应流程"},
    {"item": "人员配置", "max_score": 25, "predicted": 22, "gap": 3, "suggestion": "缺少关键人员证书编号"},
    {"item": "公司资质", "max_score": 15, "predicted": 15, "gap": 0, "suggestion": null}
  ],
  "total": {"predicted": 85, "max": 100, "rank_estimate": "前 3"}
}
```

**涉及文件**：`core/nodes/score_simulator.py`（新增）、`core/graph.py`（注册节点和边）

### 阶段 1 成功标准

- [ ] SectionGenerator 能从知识库检索并引用同类型范文
- [ ] 质量检查输出 0-100 分 + 分类问题清单，非 JSON 不再静默 PASS
- [ ] 每节内容至少检查 8000 字
- [ ] bid_type 从 GUI → AgentState → 所有消费节点可用
- [ ] 评分模拟器能预测得分并指出薄弱环节

---

## 阶段 2：质量深化

**优先级：P2**
**预计耗时：5 天**

### 2.1 定向修订替代整章重写

**当前状况**：`route_after_feedback` 返回 SectionGenerator → 整章重新生成。

**改造方案**：FeedbackProcessor 输出结构化指令：

```python
{
  "target_section": "第三章 服务方案",
  "target_paragraph": "第 3 段",
  "action": "replace_lines 12-18",
  "instruction": "补充应急响应流程图和 24 小时响应承诺",
  "reference": "参考知识库服务类范文中应急处置章节"
}
```

**涉及文件**：`core/nodes/feedback_processor.py`、`core/nodes/section_generator.py`

### 2.2 人工审核卡点

在 SectionGenerator → QualityChecker 之间增加可选的人工审核步骤：

```
生成 8 章 → GUI 逐章展示 → 用户审批 / 修改
        ↓ 用户确认后
   质量检查 → 评分模拟 → 导出
```

**GUI 交互**：
- 每章独立展开显示
- "通过" / "修改" 按钮
- 如果有疑问章节，标记为待修改
- 确认后进入质量检查阶段

**涉及文件**：`gui/panels/review_panel.py`、`core/graph.py`（新增条件路由）

### 2.3 表格/评分表结构化提取

**当前状况**：`_load_pdf` 只用 `page.get_text()`，丢失表格结构和评分表。

**改造方案**：

```python
for page in doc:
    tables = page.find_tables()
    for table in tables:
        structured.append(table.extract())  # list[list[str]]
```

结构化的评分表作为 JSON 直接传给 ReqExtractor，而非让 LLM 从纯文本猜测。

**涉及文件**：`core/nodes/doc_parser.py`、`core/nodes/req_extractor.py`

### 阶段 2 成功标准

- [ ] 质量检查发现问题后只定向修订相关段落，而非重写整章
- [ ] GUI 支持逐章审批/修改，确认后才继续管道
- [ ] PDF 表格结构被正确提取为结构化数据
- [ ] 评分表直接解析为 JSON 传给 ReqExtractor

---

## 阶段 3：产品精炼

**优先级：P3**
**预计耗时：5 天**

### 3.1 多轮迭代优化

自动执行多轮生成 → 评分 → 修订循环，选择最高分版本：

```
第 1 轮：快速生成 → 评分 72/100
第 2 轮：针对性修订 → 评分 85/100
第 3 轮：细节打磨 → 评分 92/100
→ 自动选最高分版本导出
```

### 3.2 A/B Prompt 测试

同一章节用 2 种不同的 prompt 策略生成，ScoreSimulator 自动选择高分版本。

### 3.3 管道持久化与断点续传

**当前状况**：app.invoke() 一次性跑完，用完即弃，重启后无任何历史记录。

**改造方案**：
- 每阶段保存 checkpoint 到 SQLite
- 支持查看历史运行记录
- 重启后从断点继续
- 支持比较不同次运行的结果

### 阶段 3 成功标准

- [ ] 多轮迭代自动找到最优版本
- [ ] A/B Prompt 测试框架可用
- [ ] 管道 checkpoints 持久化到 SQLite，支持断点续传

---

## 实现路线图

```mermaid
gantt
    title 标书 Agent 优化计划
    dateFormat  YYYY-MM-DD
    section 阶段零 知识库
    构建模板知识库              :done, 2026-07-01, 3d
    section 阶段一 底座
    RAG 注入                     :after 构建模板知识库, 5d
    质量检查修复                  :after 构建模板知识库, 3d
    标书类型贯穿                  :after 质量检查修复, 2d
    评分模拟器                   :after 标书类型贯穿, 3d
    section 阶段二 质量
    定向修订                      :after 评分模拟器, 3d
    人工审核卡点                  :after 定向修订, 3d
    表格结构化                    :after 人工审核卡点, 2d
    section 阶段三 精炼
    多轮迭代                      :after 表格结构化, 3d
    A/B Prompt                   :after 多轮迭代, 2d
    持久化与续传                  :after A/B Prompt, 3d
```

## 产品成熟度评分演进

| 阶段 | 评分 | 增量 | 核心提升 |
|------|:--:|:--:|----------|
| 当前 | 37 | — | 管道骨架可用 |
| 阶段 0 → 1 | 60 | +23 | 知识库有数据、质量可打分、RAG 引范文 |
| 阶段 2 | 80 | +20 | 精准修订、人工可控、表格不丢 |
| 阶段 3 | 95 | +15 | 自动优化到最佳版本、产品化 |

---

## 基础设施补充

### 依赖

| 包 | 用途 | 阶段 |
|---|------|:--:|
| `olefile` | .doc 文件最后兜底解析 | 已安装 |
| `faiss-cpu` | 向量索引和检索 | 已安装 |

### 已知约束

- 集成类 PDF（中央民大数智门户，9.7MB）在 LLM 调用时可能因上下文过大导致 OOM（实测 exit code 137），建议对大 PDF 限制传给 LLM 的文本长度为 6000-8000 字符以避免此问题。
- 背景任务的 LLM 调用链较长（每种类型约 10 次 API 请求），建议逐类型执行而非全量并行。
