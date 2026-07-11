# 任务列表 — 标书制作智能体

> Phase 1：自用验证期。按 AGENTS.md 原子任务格式，每个任务 2-5 分钟可执行。

---

## 阶段 1：项目骨架搭建

### - [ ] T001 [P] [US1] 初始化 Python 项目结构
  - 文件：`pyproject.toml` `requirements.txt` `Makefile` `.gitignore`
  - 验证：`python -c "import bid_agent"` 无 ImportError；`make test` 可运行（即使 0 个测试）
  - 耗时：3 分钟
  - 依赖：无

### - [ ] T002 [P] [US1] 定义 AgentState 数据类与枚举
  - 文件：`core/state.py`
  - 验证：`pytest tests/unit/test_state.py` 全部通过（验证 TypedDict 字段完整性、枚举值序列化）
  - 耗时：3 分钟
  - 依赖：T001

### - [ ] T003 [US1] 定义 LangGraph 状态机骨架
  - 文件：`core/graph.py`
  - 验证：`pytest tests/unit/test_graph.py::test_graph_has_all_nodes` — 验证 7 个节点 + 条件路由均已注册
  - 耗时：4 分钟
  - 依赖：T002

### - [ ] T004 [P] [US1] 实现配置加载模块
  - 文件：`config/settings.py` `config/config.yaml`
  - 验证：`pytest tests/unit/test_settings.py` — 验证从 yaml + 环境变量加载，默认值兜底
  - 耗时：4 分钟
  - 依赖：T001

### - [ ] T005 [P] [US1] 初始化 SQLite 数据库与表结构
  - 文件：`core/database.py`
  - 验证：`pytest tests/unit/test_database.py` — 验证 5 张表创建成功、CRUD 操作正确
  - 耗时：4 分钟
  - 依赖：T001

---

## 阶段 2：文档解析与需求提取

### - [ ] T006 [US1] 实现 PDF 和 DOCX 文档加载器
  - 文件：`core/nodes/doc_parser.py`
  - 验证：`pytest tests/unit/test_doc_parser.py` — 用 fixture PDF(3页) 和 DOCX(5页) 验证 chunk 切分正确（chunk_size=1000, overlap=200）
  - 耗时：5 分钟
  - 依赖：T003

### - [ ] T007 [US1] 实现需求提取节点（评分标准 + 资质 + 技术规格）
  - 文件：`core/nodes/req_extractor.py`
  - 验证：`pytest tests/integration/test_req_extractor.py` — 用 mock LLM 返回 fixture 结构化 JSON，验证 fields 完整
  - 耗时：5 分钟
  - 依赖：T003, T004

---

## 阶段 3：FAISS 检索与模板匹配

### - [ ] T008 [P] [US5] 实现 FAISS 索引管理器（建索引/增删/搜索）
  - 文件：`core/retrieval/faiss_index.py`
  - 验证：`pytest tests/unit/test_faiss_index.py` — 创建 50 条 fixture 数据建 HNSW 索引，验证 top-3 搜索返回正确结果
  - 耗时：5 分钟
  - 依赖：T001

### - [ ] T009 [P] [US5] 实现 Embedding 统一接口（OpenAI + 预留本地）
  - 文件：`core/retrieval/embeddings.py`
  - 验证：`pytest tests/unit/test_embeddings.py` — mock OpenAI API，验证返回向量维度=1536
  - 耗时：3 分钟
  - 依赖：T004

### - [ ] T010 [US5] 实现检索管线（多索引并行 + RRF 融合 + 元数据过滤）
  - 文件：`core/retrieval/pipeline.py`
  - 验证：`pytest tests/unit/test_retrieval_pipeline.py` — 双索引检索，验证 RRF 融合结果去重 + k 限制
  - 耗时：5 分钟
  - 依赖：T008, T009

### - [ ] T011 [US1] 实现模板匹配节点
  - 文件：`core/nodes/template_matcher.py`
  - 验证：`pytest tests/integration/test_template_matcher.py` — 输入标书类型，验证返回 top-3 模板 ID
  - 耗时：4 分钟
  - 依赖：T003, T010

---

## 阶段 4：章节生成

### - [ ] T012 [US1] 实现 LLM 供应商工厂（ChatOpenAI + ChatDeepSeek）
  - 文件：`core/llm/providers.py`
  - 验证：`pytest tests/unit/test_providers.py` — mock API，验证两家工厂返回 BaseChatModel 实例
  - 耗时：4 分钟
  - 依赖：T004

### - [ ] T013 [P] [US4] 实现 ModelRouter 任务级路由
  - 文件：`core/llm/router.py`
  - 验证：`pytest tests/unit/test_router.py` — mock 配置指定 QualityChecker 用 Claude，验证路由返回正确 ChatModel
  - 耗时：4 分钟
  - 依赖：T012

### - [ ] T014 [US1] 实现章节生成节点（单章节 LLM 调用 + RAG 注入）
  - 文件：`core/nodes/section_generator.py`
  - 验证：`pytest tests/integration/test_section_generator.py` — mock LLM 返回 fixture 段落，验证生成内容写入 AgentState.sections
  - 耗时：5 分钟
  - 依赖：T003, T013, T011

### - [ ] T015 [US1] 实现子图并行章节生成
  - 文件：`core/nodes/section_generator.py`（追加 subgraph 逻辑）
  - 验证：`pytest tests/integration/test_parallel_generation.py` — 验证 8 章并行生成时间 < 串行时间的 50%
  - 耗时：4 分钟
  - 依赖：T014

---

## 阶段 5：质量检查与反馈微调

### - [ ] T016 [US3] 实现质量检查节点
  - 文件：`core/nodes/quality_checker.py`
  - 验证：`pytest tests/integration/test_quality_checker.py` — mock LLM + fixture sections（含遗漏 + 违禁词），验证返回 FAIL + 具体问题清单
  - 耗时：5 分钟
  - 依赖：T003, T013

### - [ ] T017 [US2] 实现反馈处理器节点
  - 文件：`core/nodes/feedback_processor.py`
  - 验证：`pytest tests/integration/test_feedback_processor.py` — fixture 反馈「补充张三年限」，验证分类为 content_fix + 定位到 target_section
  - 耗时：5 分钟
  - 依赖：T003, T013

### - [ ] T018 [US2] 实现微调循环（条件路由 + 版本链）
  - 文件：`core/graph.py`（追加 conditional edge 逻辑）
  - 验证：`pytest tests/integration/test_feedback_loop.py` — 模拟 2 轮 FAIL→反馈→重新生成→PASS，验证 round 正确递增 + sections 版本链
  - 耗时：5 分钟
  - 依赖：T016, T017

---

## 阶段 6：文档导出

### - [ ] T019 [US3] 实现文档装配节点（章节合并 + 格式模板注入）
  - 文件：`core/nodes/doc_assembler.py`
  - 验证：`pytest tests/integration/test_doc_assembler.py` — fixture 8 章节内容，验证导出 DOCX 含目录、标题层级、仿宋/黑体字体
  - 耗时：5 分钟
  - 依赖：T003

### - [ ] T020 [US3] 实现 DOCX 格式规范注入
  - 文件：`core/nodes/doc_assembler.py`（追加格式逻辑）
  - 验证：手动打开导出的 DOCX，检查页边距（上3.7/下3.5/左2.8/右2.6cm）、行距28磅、首行缩进2字符
  - 耗时：4 分钟
  - 依赖：T019

---

## 阶段 7：Streamlit GUI

### - [ ] T021 [P] [US1] 实现 Streamlit 主入口与面板路由
  - 文件：`gui/app.py`
  - 验证：`streamlit run gui/app.py` — 本地浏览器打开，四面板标签正常切换，无 500 错误
  - 耗时：4 分钟
  - 依赖：T004

### - [ ] T022 [P] [US4] 实现模型配置面板
  - 文件：`gui/panels/config_panel.py`
  - 验证：输入 DeepSeek API Key → 点击测试连接 → 显示成功/失败；切换默认模型下拉框
  - 耗时：5 分钟
  - 依赖：T021, T013

### - [ ] T023 [P] [US1] 实现资料上传面板
  - 文件：`gui/panels/upload_panel.py`
  - 验证：拖拽上传 PDF/DOCX → 文件列表显示 → 选择标书类型下拉框 → 点击「开始生成」触发 LangGraph 管道
  - 耗时：5 分钟
  - 依赖：T021, T006

### - [ ] T024 [US1] [US2] 实现生成与审阅面板
  - 文件：`gui/panels/review_panel.py` `gui/components/progress.py` `gui/components/feedback_form.py`
  - 验证：生成中显示进度条 + 节点状态标签 → 完成后左侧章节列表可切换 → 选中段落后可提交反馈
  - 耗时：5 分钟
  - 依赖：T021, T014, T018

### - [ ] T025 [US3] 实现导出交付面板
  - 文件：`gui/panels/export_panel.py`
  - 验证：质量检查通过后 → 点击导出 DOCX → 浏览器下载文件；版本历史列表显示 v1/v2/v3
  - 耗时：4 分钟
  - 依赖：T021, T020

---

## 阶段 8：端到端集成与完善

### - [ ] T026 [US1] 端到端集成测试（Pipeline 全流程）
  - 文件：`tests/integration/test_pipeline_e2e.py`
  - 验证：用 fixture 招标文件 mock 全流程（解析→提取→匹配→生成→检查→装配→导出），验证 AgentState 在各节点正确流转
  - 耗时：5 分钟
  - 依赖：T018, T019

### - [ ] T027 [US5] 构建 6 种类型模板库索引
  - 文件：`data/faiss_index/` `templates/`
  - 验证：`pytest tests/unit/test_faiss_index.py::test_all_types_indexed` — 6 种类型各至少 3 套模板已索引
  - 耗时：4 分钟
  - 依赖：T008

### - [ ] T028 [P] [US2] 版本历史可视化
  - 文件：`gui/panels/review_panel.py`（追加 diff 展示组件）
  - 验证：提交 2 轮反馈后，版本历史列表显示 v1 初稿 → v2 修改1 → v3 修改2，每版可点击查看
  - 耗时：3 分钟
  - 依赖：T024, T018

### - [ ] T029 用真实招标文件验证
  - 文件：无代码变更（质量验证任务）
  - 验证：至少 3 份不同类型的真实招标文件走通全流程，生成完整率 > 90%
  - 耗时：5 分钟（人工验证）
  - 依赖：T026

---

## 并行执行指南

```
阶段 1（可并行）：
  T001 ─┬─ T002 ─── T003
        ├─ T004
        └─ T005

阶段 2（串行）：
  T003 → T006 → T007

阶段 3（可并行）：
  T001 → T008, T009 → T010 → T011

阶段 4（串行）：
  T004 → T012 → T013 → T011,T013 → T014 → T015

阶段 5（串行）：
  T003,T013 → T016 ─┬─ T018
                    └─ T017 ─┘

阶段 6（串行）：
  T003 → T019 → T020

阶段 7（GUI 可并行）：
  T021 ─┬─ T022
        ├─ T023
        └─ T024 → T025

阶段 8（可并行）：
  T018,T019 → T026
  T008 → T027
  T024,T018 → T028
  然后 T026 → T029
```

**总结**：29 个任务，总耗时约 130 分钟（约 2 小时纯编码时间），覆盖 Phase 1 全部功能。
