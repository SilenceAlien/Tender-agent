# 功能规范 — 劳务外包子类型分区与检索路由

> 规范版本：v1.0
> 创建日期：2026-07-11
> 签名状态：已实现，回溯补录

---

## 需求背景

### 问题

当前标书 Agent 的知识库以 `bid_type`（标书类型，如"劳务外包类"）为最小分区粒度。对于劳务外包类标书，其下存在多个差异巨大的子类型——食堂餐饮、工业生产线、HRO（人力资源外包）、保安保洁、仓储物流、BPO——它们的评分标准、服务方案结构、人员配置要求、资质门槛截然不同。

将所有子类型的范文混放在 `knowledge_base/劳务外包类/范文/` 目录下，导致：

1. **检索精度低**：搜索"sec7_service_plan"时，食堂餐饮的服务方案范文与 HRO 的服务方案范文同时返回，LLM 参考了不相关子类型的内容，生成质量下降。
2. **冷启动困难**：新增一个子类型（如"保安保洁"）时，知识库中没有任何该子类型的范文，系统只能回退到通用模板，生成内容缺乏针对性。
3. **表格结构丢失**：招标文件中的评分表、资格审查表是结构化数据，但入库时仅提取纯文本，SectionGenerator 无法利用表格结构指导生成。
4. **无版本管理**：知识库内容被直接修改后无法追踪，无法判断数据完整性是否被破坏。

### 目标

为劳务外包类构建子类型级别的知识库分区、检索路由、冷启动机制、表格结构化提取和版本管理，使每个子类型独立运作，新子类型可平滑冷启动。

### 成功标准

1. 子类型识别准确率 ≥ 90%（关键词命中 ≥ 0.8 时直接确定）
2. 检索器优先返回子类型分区的范文，仅在冷启动时回退到 `_shared` + 最近邻
3. 新子类型无数据时仍能生成可用内容（`_shared` 通用模板 + 最相似子类型参考）
4. PDF 表格被提取为结构化 JSON，供 SectionGenerator 注入提示词
5. 每个子类型分区有 `manifest.json` 记录版本号、来源、统计和内容哈希

---

## 设计

### 知识库目录结构

```
knowledge_base/劳务外包类/
├── _shared/                    # 跨子类型通用内容
│   ├── _index.json             # 通用内容索引 + Jaccard 校验记录
│   ├── chapters/               # 格式固定型章节（投标函、授权委托书等）
│   └── patterns/               # 通用表格模板
├── HRO/                        # 人力资源外包子类型分区
│   ├── _index.json             # 子类型状态（chunk_count, status）
│   ├── chunks/                 # 该子类型的章节范文 .txt 文件
│   ├── patterns/               # 该子类型的结构化表格 JSON
│   ├── prompts/                # 该子类型的专用提示词（预留）
│   └── manifest.json           # 版本管理
├── 食堂餐饮/                    # 同结构
├── 工业生产线/                  # 同结构
├── 保安保洁/                    # 同结构（预留）
├── 仓储物流/                    # 同结构（预留）
├── BPO/                        # 同结构（预留）
├── 范文/                        # 兼容现有：无子类型时回退到此目录
├── 招标文件/
├── 中标公告/
├── 中标方案/
└── 模板/
```

### 子类型清单

| 子类型代码 | 中文名 | 关键词示例 |
|-----------|--------|-----------|
| `食堂餐饮` | 食堂餐饮外包 | 食堂、餐饮、食材、供餐、HACCP |
| `工业生产线` | 工业生产线劳务 | 生产线、车间、焊接、装配、驻厂 |
| `保安保洁` | 保安保洁服务 | 保安、保洁、安保、巡逻、物业管理 |
| `仓储物流` | 仓储物流外包 | 仓储、物流、分拣、装卸、供应链 |
| `HRO` | 人力资源外包 | 人力资源、薪酬代发、社保代理、人事代理 |
| `BPO` | 业务流程外包 | 业务流程、呼叫中心、数据处理、客服外包 |

### 补强一：三层子类型识别

**触发条件**：`ReqExtractor` 检测到 `bid_type` 包含"劳务外包"或"劳务管理服务"时触发。

**识别流程**：

```
Tier 1: 关键词快速匹配（确定性）
    → 计算各子类型关键词命中率 = 命中关键词数 / 总关键词数
    → 最高命中率 ≥ 0.8 → 直接返回（高置信）
    → 最高命中率 < 0.8 → 进入 Tier 2

Tier 2: LLM 辅助判断（模糊消歧）
    → 前 3000 字 + 关键词命中矩阵 → LLM
    → confidence ≥ 0.7 → 返回结果
    → confidence < 0.7 → 进入 Tier 3

Tier 3: 用户确认（兜底，由 InfoVerificationGate 处理）
    → 返回 Top-3 候选 + 置信度 + LLM 理由
    → 用户在 InfoVerificationGate 界面选择确认
```

**输出**：`bid_subtype` 字段写入 `AgentState`，贯穿后续所有节点。

### 补强二：`bid_subtype` 贯穿全管道

`bid_subtype` 在管道中的流转路径：

```
ReqExtractor
    │ detect_subtype() → bid_subtype 写入 state
    ↓
InfoVerificationGate
    │ 展示 bid_subtype 供用户确认 + 冷启动状态提示
    │ 用户可修改 → user_confirmed_fields["bid_subtype"]
    ↓
SectionGenerator
    │ 读取 bid_subtype → 传递给 _get_section_prompt() 和 _retrieve_reference()
    ↓
ReferenceRetriever
    │ 读取 bid_subtype → 优先搜索子类型 chunks/ 目录
```

**`bid_type` 归一化**：当 `bid_type` 为"劳务管理服务类"时，知识库路径归一化为"劳务外包类"（`kb_type = "劳务外包类" if "劳务" in bid_type else bid_type`）。

### 补强三：`_shared` 严格准入与权重控制

**准入白名单**：只有格式固定型章节（由法规/模板决定，不含子类型专有术语）才可进入 `_shared/`：

| 章节键 | 说明 |
|--------|------|
| `sec1_bid_letter` | 投标函 — 格式固定 |
| `sec2_legal_rep` | 法定代表人身份证明 — 格式固定 |
| `sec3_authorization` | 授权委托书 — 格式固定 |
| `sec4_deposit` | 投标保证金 — 格式固定 |
| `sec5_deviation` | 商务和技术偏差表 — 格式固定 |
| `ch1_letter` | 通用：投标函 |
| `ch2_authorization` | 通用：授权委托书 |

服务方案、技术方案等差异大的章节**禁止**进入 `_shared/`。

**检索权重控制**（根据子类型数据量动态调整）：

| chunk_count | subtype 权重 | _shared 权重 | neighbor 权重 | 说明 |
|-------------|:------------:|:------------:|:-------------:|------|
| 0（冷启动） | 0.0 | 0.7 | 0.3 | 无自身数据，依赖通用+最近邻 |
| 1-4（不足） | 1.0 | 0.3 | 0.0 | 自身为主，通用补充 |
| ≥5（正常） | 1.0 | 0.0 | 0.0 | 独立运作，不降级 |

### 补强四：新子类型完整冷启动链路

当子类型分区 `chunks/` 为空时（chunk_count = 0）：

1. **检索回退**：不搜索子类型分区，搜索 `_shared/chapters/`（仅格式固定型章节）+ 范文目录
2. **最近邻推荐**：计算该子类型与所有已有子类型的关键词 Jaccard 相似度，返回 Top-2 最近邻
3. **用户提示**：`InfoVerificationGate` 展示冷启动状态和提示信息，建议用户入库更多真实标书

**冷启动状态分类**：

| 状态 | 条件 | 用户提示 |
|------|------|---------|
| `cold_start` | chunk_count = 0 | "该子类型知识库无数据，生成内容将参考通用模板+最相似子类型" |
| `data_insufficient` | chunk_count 1-4 | "该子类型数据不足，建议入库更多真实标书以提升质量" |
| `normal` | chunk_count ≥ 5 | 无提示 |

### 补强五：表格结构化提取与入库

**入库阶段**（`ingest_real_bid.py --subtype`）：

使用 PyMuPDF 的 `page.find_tables()` API 从 PDF 中提取表格，自动推断表格类型并保存为 JSON：

| 表格类型 | 识别关键词 | JSON 文件名格式 |
|---------|-----------|----------------|
| `scoring` | 评分、评审、打分、分值 | `scoring_p{页码}_{序号}_{来源}.json` |
| `qualification` | 资格、资质、审查、准入 | `qualification_p{页码}_{序号}_{来源}.json` |
| `pricing` | 报价、价格、金额、单价 | `pricing_p{页码}_{序号}_{来源}.json` |
| `staffing` | 人员、岗位、人数 | `staffing_p{页码}_{序号}_{来源}.json` |
| `unknown` | 未匹配（≥3 行才保存） | `unknown_p{页码}_{序号}_{来源}.json` |

**JSON 数据结构**：

```json
{
  "source": "广晟真实标书",
  "subtype": "HRO",
  "page": 12,
  "table_index": 0,
  "type": "qualification",
  "header": ["序号", "资质名称", "要求", "响应情况"],
  "rows": [["1", "营业执照", "提供复印件", "已提供"]],
  "row_count": 1
}
```

**消费阶段**（`SectionGenerator._inject_table_template`）：

当生成资格审查章节（`sec6_qualification` / `ch6_qualifications`）且 `bid_subtype` 存在时，从 `patterns/` 目录加载 `qualification` 和 `unknown` 类型表格，转为 Markdown 格式注入提示词。

### 补强六：子类型分区版本管理

每个子类型分区维护 `manifest.json`：

```json
{
  "subtype": "HRO",
  "version": "0.2.0",
  "last_updated": "2026-07-11",
  "stats": {
    "chunk_count": 14,
    "source_count": 2,
    "pattern_count": 5,
    "prompt_count": 0
  },
  "sources": [
    {
      "project_slug": "广晟真实标书",
      "chunk_count": 7,
      "table_count": 3,
      "ingested_at": "2026-07-11"
    }
  ],
  "patterns_hash": "a1b2c3d4e5f6",
  "chunks_hash": "f6e5d4c3b2a1"
}
```

**版本号规则**（语义化版本）：

| 动作 | 版本变化 | 触发场景 |
|------|---------|---------|
| `major` | X+1.0.0 | 结构变更（如目录重组） |
| `minor` | X.Y+1.0 | 新增源标书入库 |
| `patch` | X.Y.Z+1 | 修正内容 |

**哈希校验**：`compute_dir_hash()` 计算目录下所有 `.txt`/`.json`/`.md` 文件的 SHA-256 哈希（截取前 12 位），`verify_manifest()` 对比记录值与实际值，检测未经入库脚本的直接修改。

---

## 用户故事

### 用户故事 7 — 子类型智能识别与分区检索（优先级：P1）

作为投标负责人，我希望系统能自动识别劳务外包标书的具体子类型（如食堂餐饮、HRO），使用该子类型专有的范文和表格模板生成标书，而不是混用所有子类型的内容。

**为什么这个优先级**：子类型混用是当前生成质量的最大瓶颈——食堂餐饮的服务方案参考了 HRO 的范文，导致内容偏离招标文件要求。

**独立测试**：上传一份 HRO 类型的招标文件 → 系统自动识别子类型为"HRO" → InfoVerificationGate 展示确认 → 生成时优先使用 HRO 分区的范文和表格模板。

**验收场景**：

1. **给定** 一份包含"人力资源""薪酬代发""社保代理"等关键词的招标文件，**当** ReqExtractor 执行子类型识别，**那么** `bid_subtype = "HRO"`，识别方法为"keyword"，置信度 ≥ 0.8

2. **给定** 一份关键词不明确的招标文件（如同时涉及食堂和保洁），**当** 关键词命中率 < 0.8，**那么** 进入 Tier 2 LLM 判断，LLM 返回 confidence ≥ 0.7 的结果

3. **给定** LLM 返回 confidence < 0.7，**当** InfoVerificationGate 展示，**那么** 用户看到 Top-3 候选子类型及置信度，可选择确认或修改

4. **给定** bid_subtype = "HRO" 且 HRO 分区有 7 个 chunk，**当** SectionGenerator 生成服务方案章节，**那么** 检索器优先从 `HRO/chunks/` 搜索范文，不返回食堂餐饮的范文

5. **给定** bid_subtype = "保安保洁" 且该分区为空（冷启动），**当** SectionGenerator 生成投标函，**那么** 检索器从 `_shared/chapters/` 搜索通用投标函模板

6. **给定** bid_subtype = "保安保洁" 且该分区为空，**当** InfoVerificationGate 展示，**那么** 用户看到冷启动提示和 Top-2 最近邻子类型推荐

7. **给定** 入库脚本以 `--subtype HRO` 参数运行，**当** PDF 中有评分表和资格审查表，**那么** 表格被提取为 JSON 保存到 `HRO/patterns/`，且 `manifest.json` 版本号递增

8. **给定** HRO 分区有资格审查表 JSON，**当** SectionGenerator 生成资格审查章节，**那么** 提示词中包含结构化表格模板（Markdown 格式）

9. **给定** 某子类型分区的 `manifest.json` 记录了 chunks_hash，**当** 有人直接修改了 chunks/ 中的文件，**那么** `verify_manifest()` 返回 `valid: false` 并报告哈希不一致

---

## 技术计划

### 组件清单

| 组件 | 文件 | 职责 |
|------|------|------|
| SubtypeRouter | `core/retrieval/subtype_router.py` | 三层识别、`_shared` 准入、权重控制、冷启动、manifest |
| ReferenceRetriever（修改） | `core/retrieval/reference_retriever.py` | 子类型分区优先检索 + bid_type 归一化 |
| ReqExtractor（修改） | `core/nodes/req_extractor.py` | 调用 detect_subtype，写入 bid_subtype |
| InfoVerificationGate（修改） | `core/nodes/info_verification_gate.py` | 展示 bid_subtype + 冷启动状态 |
| SectionGenerator（修改） | `core/nodes/section_generator.py` | 传递 bid_subtype + 表格模板注入 |
| AgentState（修改） | `core/state.py` | 新增 bid_subtype 字段 |
| 入库脚本 | `scripts/ingest_real_bid.py` | 子类型分区存储 + 表格提取 + manifest 更新 |

### 入库脚本用法

```bash
# 入库到子类型分区（推荐）
python3.10 scripts/ingest_real_bid.py \
    --pdf "投标文件.pdf" \
    --label "广晟真实标书" \
    --subtype HRO

# 入库到范文目录（兼容现有，无子类型）
python3.10 scripts/ingest_real_bid.py \
    --pdf "投标文件.pdf" \
    --label "广铁真实标书"
```

`--subtype` 非空时：
- 章节范文保存到 `{subtype}/chunks/`
- 格式固定型章节同步到 `_shared/chapters/`
- PDF 表格提取到 `{subtype}/patterns/`
- `manifest.json` 版本号递增

### 依赖

无新依赖。PyMuPDF (`fitz`) 的 `find_tables()` API 已在现有依赖中。

---

## 边界情况处理

| 场景 | 处理方式 |
|------|---------|
| `bid_type` 为"劳务管理服务类"而非"劳务外包类" | 归一化：`"劳务" in bid_type` → KB 目录映射到"劳务外包类" |
| 子类型识别完全失败（所有关键词命中率 0） | Tier 3 返回空 `bid_subtype`，InfoVerificationGate 让用户手动选择 |
| `_shared/chapters/` 中无对应章节文件 | 检索器回退到 `范文/` 目录搜索 |
| PDF 无表格（`find_tables()` 返回空） | `extract_and_save_tables` 返回 0，不报错，manifest 正常更新 |
| 同一来源重复入库 | `save_chunks` 先清理同 label 旧文件再保存，manifest 追加新 source 记录 |
| `manifest.json` 不存在 | `update_manifest` 创建初始版本 `0.1.0` |
| 子类型不在预定义清单中 | 关键词命中率为 0，进入 Tier 3 用户确认；用户可手动输入 |
| 表格类型无法推断（unknown）且行数 < 3 | 跳过不保存，避免无意义小表格污染 patterns |

---

## 与已有规范的关系

| 规范 | 关系 |
|------|------|
| `specs/001-bid-agent/spec.md` | 基础管道架构 — 本规范在此基础上新增子类型维度 |
| `specs/005-optimization-plan/spec.md` | 阶段1-3优化 — 本规范是其 95+ 分优化的后续深化 |
| `specs/006-info-verification-gate/spec.md` | 信息校验门 — 本规范在其基础上新增 bid_subtype 确认字段和冷启动提示 |
| `specs/007-consistency-evolution/spec.md` | 一致性自进化 — 本规范的子类型分区为一致性检查提供更精确的上下文 |
