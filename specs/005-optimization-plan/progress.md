# 会话进度 — 产品优化方案

## 2026-07-01 会话 1

### 完成
- [x] 全量分析：评估当前标书 Agent 的 10 个关键短板
- [x] 三阶段优化方案设计（阶段 0/1/2/3）
- [x] 产品成熟度初始评分：37/100
- [x] 阶段 0 执行：构建知识库模板脚本
- [x] 知识库填充：6 种类型 × (1 模板 + 8 范文) = 54 个文件，18MB
- [x] 规范文档：`specs/005-optimization-plan/spec.md`

### 关键决策
- 知识库数据缺失是根本问题，SSR 评估因此下调至 37 分
- 选择用 LLM 反向生成模板（而非人工撰写），因无真实中标方案可用
- 敏感数据留白方案：统一 `【待填写：具体说明】` 格式，避免编造数据

### 错误
| 错误 | Strike | 方案 |
|------|--------|------|
| 背景任务因 session 超时被 kill | 1 | 改用 `--type` 逐一执行 |
| 集成类 9.7MB PDF 解析 OOM | 1 | 单独取出 ch8 用独立脚本生成 |
| `--essays-only` 模式 `sources` 未定义 | 1 | 改为 `BID_TYPE_SOURCES.get()` 修复 |

### 下次
- [ ] 阶段 1.1：RAG 注入 SectionGenerator
- [ ] 阶段 1.2：质量检查三大漏洞修复
- [ ] 阶段 1.4：评分模拟器节点

---

## 2026-07-03 会话 2 — 工作流评审

### 完成
- [x] 逐文件代码审查：graph.py / state.py / 7 个节点 / retrieval / evolution / pipeline_runner
- [x] 数据流追踪：验证各节点输入输出是否真正被下游消费
- [x] 反馈循环验证：确认 FeedbackProcessor 输出从未被 SectionGenerator 读取
- [x] 评审报告：`specs/005-optimization-plan/workflow-review.md`
- [x] 综合打分：当前 34/100，spec.md 方案 62/100

### 关键发现（spec.md 遗漏的 9 个漏洞）
- P0 致命：LLM 注入断裂（3/4 节点走 mock）
- P0 致命：反馈循环完全失效（SectionGenerator 跳过已生成章节，重试空转）
- P0 致命：TemplateMatcher 输出从未被消费
- P1 严重：质量检查完成性启发式过宽
- P1 严重：evolution 模块孤立未接入
- P1 严重：req_extractor 无输入长度限制（OOM 风险）
- P1 严重：并行生成无错误隔离
- P2：node_status FAILED 无早期路由
- P2：feedback_history 三层压缩未实现

### 关键决策
- 在 spec.md 阶段 1 之前插入"紧急修复"阶段（2 天），先接通断裂的数据流
- 定向修订（spec.md 阶段 2.1）提升为 P0，与紧急修复合并
- 表格结构化提取（spec.md 阶段 2.3）提升为 P1

### 下次
- [ ] 紧急修复：LLM 注入（functools.partial 或 RunnableConfig）
- [ ] 紧急修复：反馈循环接通（SectionGenerator 读取 feedback_history）
- [ ] 紧急修复：模板消费接通（selected_template_id 写入与读取）

---

## 2026-07-03 会话 3 — 落地管道扩展分析

### 完成
- [x] 对照真实标书生产全流程，识别当前管道缺失的 10 个环节
- [x] 设计落地版 8 层分层管道（L0 入库 / L1 决策 / L2 需求 / L3 策略 / L4 生成 / L5 校验 / L6 人工 / L7 封装）
- [x] 三维度评分（废标风险/中标影响/法律必须）量化每个环节的落地阻碍度
- [x] 落地管道对比图 + 阻碍度柱状图
- [x] 落地扩展方案文档：`specs/005-optimization-plan/landing-pipeline-expansion.md`

### 关键结论
- 当前管道是"格式转换器"（PDF→DOCX），落地需要"生产链"（带门禁+卡点+三道校验）
- 缺失 10 环节中 4 个是 MVP 必须（P0）：EligibilityChecker / ComplianceChecker / HumanReviewGate / CrossReferenceChecker
- 最大非技术阻碍是数据：企业资质库、历史业绩库、法务规则库都需准备
- 落地 MVP 约 16 天（紧急修复 2 + spec.md 深化 5 + 新增 4 节点 9），成熟版约 5 周

### 新增节点设计要点
- EligibilityChecker：L1 决策门禁，资质不满足直接终止（避免浪费生成成本）
- CrossReferenceChecker：跨章节一致性（人员数/技术参数/时间节点/金额跨章一致）
- ComplianceChecker：格式合规 + 法律合规（绝对化用语/虚假业绩/知识产权）
- HumanReviewGate：法律必须，法定代表人签字，不可全自动化
- PricingStrategist：报价策略（当前完全缺失，是标书核心）
- SealingExporter：电子签章 + 加密 PDF（依赖第三方 SDK）

### 下次
- [ ] 启动紧急修复阶段
- [ ] 准备企业资质库数据结构
- [ ] 定义法务合规规则库

---

## 2026-07-03 会话 4 — 系统升级改造执行

### 完成
- [x] Phase A1: LLM 注入修复（functools.partial 预绑定 llm_fn）
- [x] Phase A2: 反馈循环接通（读取 feedback_history，定向重生成 target_section）
- [x] Phase A3: 模板消费接通（TemplateMatcher 写 selected_template_id，SectionGenerator 读取）
- [x] Phase B1: 质量检查三大漏洞修复（截断 2000→8000、非JSON→FAIL、完成性启发式修复、0-100评分）
- [x] Phase B2: 集成优化 Prompts（劳务管理服务类使用真实标书结构的8章prompt）
- [x] Phase B3: bid_type 贯穿全管道（AgentState→所有节点）
- [x] Phase B4: ScoreSimulator 评分模拟器节点（新增，预测得分+薄弱环节+排名）
- [x] Phase B5: 工程健壮性（req_extractor 输入截断、早期失败路由、并行错误隔离）
- [x] Phase C1: EligibilityChecker 资质门槛校验（新增，FAIL→终止管道）
- [x] Phase C2: ComplianceChecker 合规审查（新增，绝对化用语+虚假业绩检测）
- [x] Phase C3: CrossReferenceChecker 跨章节一致性（新增，人员数/金额/日期一致）
- [x] Phase D: 全量测试 357 通过 / 3 失败（FAISS 预存问题），0 回归
- [x] 端到端管道验证：11 节点全部执行，劳务管理服务类标书完整跑通

### 关键决策
- LLM 注入用 functools.partial 而非 RunnableConfig，更简单且向后兼容
- 反馈循环改为"有反馈的章节强制重生成"而非"删除后重生成"，保留无反馈章节
- 合规审查的"第一/唯一/最"用上下文敏感正则避免误报（第一章≠第一）
- 跨章节一致性阈值：人员数≥3 才比较（过滤1-2人的子团队计数）
- 管道从 7 节点扩展到 11 节点（+EligibilityChecker/CrossReferenceChecker/ComplianceChecker/ScoreSimulator）

### 新增文件
- core/nodes/optimized_prompts.py（从 specs 迁移）
- core/nodes/score_simulator.py
- core/nodes/eligibility_checker.py
- core/nodes/compliance_checker.py
- core/nodes/cross_reference_checker.py
- data/company_quals.json（企业资质库模板）

### 修改文件
- core/graph.py（LLM 注入 + 4 新节点 + 5 条件路由）
- core/state.py（bid_type + 4 新状态字段 + ALL_NODES 11 节点）
- core/nodes/section_generator.py（反馈循环 + 模板消费 + bid_type + 错误隔离）
- core/nodes/quality_checker.py（3 漏洞修复 + 0-100 评分）
- core/nodes/req_extractor.py（输入长度限制）
- core/nodes/template_matcher.py（写 selected_template_id）
- gui/panels/review_panel.py（传 llm_fns 给 build_graph）

### 错误
| 错误 | Strike | 方案 |
|------|--------|------|
| pytest 未安装 | 1 | pip install pytest |
| python-docx/faiss-cpu 未安装 | 1 | pip install 全部依赖 |
| langchain_openai 缺失 | 1 | pip install langchain-openai |
| CrossReferenceChecker 人员检测阈值过高 | 1 | 改为≥3人比较 + 每节取最大值 |

### 下次
- [ ] 表格结构化提取（doc_parser.py 的 find_tables）
- [ ] RAG 注入 SectionGenerator（FAISS 检索同类型范文）
- [ ] HumanReviewGate 人工审核卡点
- [ ] PricingStrategist 报价策略节点
- [ ] evolution 模块接入 SectionGenerator

---

## 2026-07-11 会话 5 — 95+优化方案执行完成 + Lint 修复

### 完成
- [x] 95+优化方案全部 6 项补强执行完成
  - 补强一：三层子类型识别（关键词→LLM→用户确认）→ `subtype_router.py`
  - 补强二：`bid_subtype` 贯穿全管道（state→req_extractor→info_verification_gate→section_generator→reference_retriever）
  - 补强三：`_shared` 严格准入 + 权重控制（格式固定型章节才入 `_shared/`）
  - 补强四：新子类型完整冷启动链路（chunk_count=0 → _shared 0.7 + 最近邻 0.3）
  - 补强五：表格结构化提取与入库（`extract_and_save_tables` → JSON patterns/）
  - 补强六：子类型分区版本管理（`manifest.json` + 哈希校验）
- [x] 两份真实标书 PDF 入库（广铁 + 广晟），按子类型分区存储
- [x] 子类型目录初始化（HRO / 食堂餐饮 / 工业生产线），含 `_index.json` + `manifest.json`
- [x] 全量测试通过：**507 passed, 0 failed**
- [x] Lint 修复：32 个 F 级别错误 → 0 个（25 自动修复 + 7 手动修复）
  - F821: 实现 `extract_and_save_tables` 和 `update_subtype_manifest` 两个缺失函数
  - F841: 移除 4 个未使用变量（`detail_lower`, `root_indent`, `title_fonts`, `heading_level`, `has_new`）
  - F401: 自动移除未使用导入（+ 恢复 `apply_user_corrections` 重导出并加 `# noqa: F401`）
  - F541: 自动移除无占位符的 f-string 前缀
  - F811: 自动移除 `difflib` 重复导入

### 关键决策
- `save_chunks` 新增 `bid_subtype` 参数：非空时存入子类型 `chunks/` 目录，同时将格式固定型章节同步到 `_shared/chapters/`
- `extract_and_save_tables` 使用 PyMuPDF 的 `find_tables()` API，自动推断表格类型（scoring/qualification/pricing/staffing）
- `update_subtype_manifest` 复用 `SubtypeRouter.update_manifest()` 进行版本管理，避免重复实现
- `apply_user_corrections` 在 `graph.py` 中标记 `# noqa: F401` 作为重导出，供测试导入

### 修改文件
- `scripts/ingest_real_bid.py` — 新增 `extract_and_save_tables`、`update_subtype_manifest`，修复 `save_chunks` 签名，移除未使用变量
- `core/graph.py` — 恢复 `apply_user_corrections` 重导出（`# noqa: F401`）
- `core/evolution/consistency_lesson_extractor.py` — 移除未使用变量 `detail_lower`
- `core/nodes/doc_assembler.py` — 移除未使用变量 `root_indent`、`title_fonts`、`heading_level`
- 多个文件 — 自动修复 F401/F541/F811（ruff --fix）

### 错误
| 错误 | Strike | 方案 |
|------|--------|------|
| `test_two_phase_pipeline.py` 导入 `apply_user_corrections` 失败 | 1 | 恢复 `graph.py` 中导入并加 `# noqa: F401` |

### 下次
- [ ] 端到端验证：使用真实招标文件测试完整管道流程
- [ ] FAISS 语义检索启用（当前为关键词检索）
- [ ] GUI 界面适配 `bid_subtype` 选择
