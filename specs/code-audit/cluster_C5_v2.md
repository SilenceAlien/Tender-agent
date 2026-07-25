# 簇 C5 — 复审计报告（v2）

> 审计对象（同 C5）：`bid-agent/core/nodes/doc_assembler.py`（1512 行，全文逐行 Read 1-1512）+ `bid-agent/core/state.py`（256 行），对照 `system-workflow.md` 节点 ⑭ DocumentAssembler（L496-536）及 ComplianceChecker 页眉页脚检查（L424）。
> 审计方法：逐行 Read `doc_assembler.py`（分段 offset 读完所有 1512 行）+ Grep 未使用、核对 ⑭ 全部 11 项装配策略；逐项核对 C5 簇 4 个旧问题，并查找回归/遗漏。
> 结论概览：**4 项旧问题中 1 项已修复（M8 页边距），0 项部分修复，3 项未修复（H7 连续页码/页眉页脚/Mermaid力导向/目录页码、M9 文件名契约、M10 占位符正则）**；并发现 **1 项高危回归 N1（Mermaid 仍树形布局，非规范要求的力导向）+ 2 项中危新问题（N2 假目录无真实页码、N3 v{round} 起点为 0）**。

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/doc_assembler.py` | 1512 | ⑭ DocumentAssembler：Mermaid 解析/渲染、Markdown→Word、占位符红粗、DOCX 装配与导出 |
| `bid-agent/core/state.py` | 256 | AgentState（TypedDict）字段定义 + 工厂；核对 project_id 是否存在 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准，节点 ⑭ 在 L496-536） |

## 2. 旧问题修复核对表（C5 簇）

| 旧问题 | 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|--------|----------|---------------------|------|------|
| **H7** 连续页码缺失 + 页眉页脚未从 format_rules 读取 + Mermaid 树形非力导向 + 目录页码硬编码 | ⑭「连续页码」「页眉页脚支持自定义」「Mermaid 力导向」「目录自动生成含页码」 | `doc_assembler.py:1119-1121`（页眉页脚注释掉）、`:1199`（目录硬编码`第{i}页`）、`:381-447`/`:636-662`（树形布局）、`:263`（文本 fallback 树形） | ❌ **未修复** | 4 个子项全部未动。① 无任何 PAGE 域/连续页码插入（`doc.sections` 循环 L1114-1118 只设页边距）；② 页眉页脚代码被注释（L1119-1121），从不读 `format_rules`；③ Mermaid 两个渲染器均用分层树布局（`_assign` 叶子顺序排 x、父节点居中于子节点之上），非规范要求的**力导向算法**；④ 目录页码 `f"{section_name}\t第{i}页"`（L1199）仍是顺序硬编码序号。 |
| **M8** 页边距默认值冲突（旧 下3.5/右2.6 vs 规范上下3.7/左右2.8） | ⑭「默认上下3.7cm/左右2.8cm」 | `doc_assembler.py:27`、`:46` | ✅ **已修复** | `DEFAULT_FORMAT["page_margin"]`（L27）已改为 `"上3.7cm/下3.7cm/左2.8cm/右2.8cm"`；`_format_margin` 默认值（L46）`{"top":3.7,"bottom":3.7,"left":2.8,"right":2.8}` 与规范一致。L511 处旧注释已移至 L1119。 |
| **M9** 导出文件名用时间戳，违背 `{project_id}_标书_v{round}` | ⑭ 契约「Export to data/exports/{project_id}_标书_v{round}.docx」 | `doc_assembler.py:1475-1490` | ❌ **未修复** | 虽改为优先取 `project_contract.get("project_id")` 再 `requirements.bid_number`，但：① `AgentState` **无 `project_id` 字段**（`state.py:66-188`，全文件无该键），`project_contract` 亦无此键（ContractExtractor 只产出 bidder_name/project_location/...），故实际依赖 `bid_number`；② 当 `bid_number` 为空时仍**回退时间戳**（`_标书_v{round}.docx` 契约被违背）；③ 契约要求 `project_id`，代码用 `bid_number` 替代，语义不符。 |
| **M10** 占位符正则 `(【待填写[^】]*】)` 未强制冒号且允许空内容 | ⑭「正则匹配：`【待填写[：:][^】]+】`」 | `doc_assembler.py:1230` | ❌ **未修复** | 正则仍为 `re.compile(r"(【待填写[^】]*】)")`：用 `*` 允许**空内容** `【待填写】`，且**不要求冒号**。规范（system-workflow.md:531）要求 `【待填写[：:][^】]+】`（冒号必选 + `[^】]+` 至少一个字符）。宽松正则原样保留，红粗渲染会误把空占位符当占位符上色。 |

## 3. 规范整体符合度核对（节点 ⑭，L496-536）

| 规范条款 | 状态 | 证据 / 说明 |
|----------|------|------------|
| 字体 正文仿宋_GB2312、标题黑体/楷体 | ✅ | `font_map` 映射「正文仿宋_GB2312」→「仿宋_GB2312」（L1128-1133），正文实际字体设置正确；标题黑体/楷体（L860 `黑体 if level<=2 else 楷体`）。**前提**：依赖系统已安装「仿宋_GB2312」「黑体」「楷体」字体，未做内嵌/回退（环境风险，见 N4）。 |
| 字号 正文小四(12pt)、标题 16→14→13→12 | ✅/⚠️ | 正文 `Pt(12)`（L1135）；`_add_markdown_heading` 字号映射 16/14/13/12（L861）符合；但**章节标题**（L1219-1224）固定 `Pt(16)`+黑体，未按章节层级递减。 |
| 页边距 默认上下3.7/左右2.8 | ✅ | 见 M8，已修复。 |
| 行距 默认 28 磅固定 | ✅ | `pf.line_spacing = Pt(28)`（L1156），图片段临时改单倍距（L1281，有意为之）。 |
| 目录 自动生成（Tab 前导符 + 页码） | ❌ | 仅手工拼「名称 + 点前导 Tab + 硬编码序号」（L1180-1202），**非真实 Word TOC 域**，页码为伪造顺序号（见 H7/N2）。 |
| 页码 连续页码 | ❌ | 无 PAGE 域插入（见 H7）。 |
| 页眉页脚 支持自定义 | ❌ | 代码注释掉（L1119-1121），不读 `format_rules`，ComplianceChecker 检查的页眉页脚项（system-workflow.md:424）实际无产出。 |
| Markdown→Word 标题/表格/列表/加粗 | ✅ | 表格 `_add_table_to_docx`（L949-1003，含表头底色）、列表 L881-919、加粗 `_add_runs_with_placeholders`（L1044-1055）、`<br>` 转换行 L1025 均实现。 |
| Mermaid 力导向渲染为图片插入 | ❌ | 两个渲染器均为**分层树布局**（L381-447 / L636-662），文本兜底也是树（L263）。规范 L526 明确要求「力导向算法计算节点位置」。 |
| 占位符 红色加粗 `【待填写：...】` | ⚠️ 部分 | 红粗渲染逻辑正确（L1066-1080 RGBColor(0xFF,0,0)+bold），但**匹配正则过宽**（M10，`[^】]*` 允许空且不强制冒号）。 |
| 签章页 显示 seal_requirement | ✅ | 末页 `（{seal_requirement}）`（L1419-1425），缺省「加盖公章」来自 DEFAULT_FORMAT。 |

## 4. 新发现问题（回归 / 遗漏）

### 高
- **N1 — 回归/未修复：Mermaid 仍用树形布局，非规范要求的力导向算法**
  `doc_assembler.py:381-447`（`_mermaid_to_png_matplotlib` 内 `_assign`）与 `:636-662`（`_mermaid_to_png_pillow` 内 `_assign`）均实现「叶子顺序排 x、父节点取子节点 x 均值居中于上一层」的**分层树/hierarchical 布局**；文本兜底 `:263` 亦为树形。规范 ⑭（system-workflow.md:526）明确要求「**力导向算法**计算节点位置」。两渲染器与回退全链路均未使用力导向（无斥力/引力迭代、无 `networkx.spring_layout` 等）。该问题在上一轮属 H7 子项，本轮修复只动了页边距与文件名，**Mermaid 力导向完全未实现**——属规范硬偏离，且因「先试 PNG→失败才兜底文本」，绝大多数导出走的是树形 PNG。

### 中
- **N2 — 目录为假列表、无真实页码（H7 子项延伸）**
  `doc_assembler.py:1180-1202` 用 `for i, section_name` 手工生成「章节名 + Tab 点前导 + `第{i}页`」，既非 Word 自动目录域（无 `TOC` 域、无 `PAGEREF`），页码也是 1..N 的顺序假序号，与真实分页无关。规范 ⑭ 要求「自动生成目录含页码」。Word 打开后目录不随内容更新、页码全错。建议改用 `python-docx` 插入 TOC 域（需配套更新域）或至少基于真实分节计算页码。
- **N3 — 文件名用 `v{current_round}`，首轮导出为 `v0`（M9 延伸 + 潜在 off-by-one）**
  `doc_assembler.py:1488,1490` 文件名写 `f"{safe_id}_标书_v{current_round}.docx"`，而 `current_round` 初值 0（`state.py:241`）。即首次导出为 `..._标书_v0.docx`，与通常「v1 起算」直觉及契约 `v{round}` 预期不符；且无 `round` 字段，用 `current_round` 近似。建议首轮映射为 `v1`（或明确 `round = current_round + 1`）。

### 低 / 运行时风险（需人工确认）
- **N4 — 字体强依赖系统安装，无内嵌/回退（运行时风险）**
  `doc_assembler.py:1128-1146, 860, 1328` 直接写「仿宋_GB2312/黑体/楷体」East-Asia 字体名，若运行环境（如 Linux CI 服务器）未安装这些字体，Word 会静默替换，导致实际导出非政府采购标准字体。**需人工确认**目标导出环境已安装对应字体，或增加字体存在性校验/回退。
- **N5 — `title_levels` 逻辑冗余且恒为空（死条件，低危）**
  `doc_assembler.py:1214-1217`：`if title_levels and len(title_levels) > 0:` 内又写 `heading_font = title_levels[0] if len(title_levels) > 0 else "黑体"`，条件重复；且 `DEFAULT_FORMAT["title_levels"]=[]`（L30），实际恒走 `else: heading_font="黑体"`。章节标题层级永远黑体 16pt（见 §3 字号说明），不致命但属无效配置分支，建议删除死代码或真正消费 `title_levels`。
- **N6 — 占位符空内容被误判为占位符渲染红粗（M10 延伸）**
  `doc_assembler.py:1040` `placeholder_re.fullmatch(part)` 配合 `*` 量词，会把 `【待填写】`（空内容）判为占位符并红色加粗（L1066-1080）。规范 `【待填写[：:][^】]+】` 要求非空且带冒号，此处宽松正则导致「空待填写」也上色，属 M10 未修复的连带表现。

## 5. 修复状态结论

| 项目 | 结论 |
|------|------|
| 已修复 | **M8**（页边距默认改为上下3.7/左右2.8，`doc_assembler.py:27,46`） |
| 未修复 | **H7**（连续页码/页眉页脚自定义/Mermaid力导向/目录页码硬编码，L1119-1121,1199,381-447,636-662,263）、**M9**（`project_id` 仍缺失+时间戳回退违背契约，L1475-1490；`state.py` 无 project_id 字段）、**M10**（占位符正则仍宽松 `[^】]*`、无冒号强制，L1230） |
| 新缺陷 | **N1 高**：Mermaid 全链路树形布局，未实现规范力导向（L381-447/636-662/263）；**N2 中**：目录为假列表、无真实页码（L1180-1202）；**N3 中**：`v{current_round}` 首轮为 v0（L1488,1490 + state.py:241） |

**优先处理建议**：① 落实 H7 四项——插入连续页码 PAGE 域、从 `format_rules` 读页眉页脚、Mermaid 改用 `networkx.spring_layout` 等力导向、目录接入真实页码（或 Word TOC 域）；② 修 M9——在 `AgentState`/`project_contract` 补 `project_id` 字段并去除时间戳回退，或明确契约别名；③ 修 M10——正则收紧为 `【待填写[：:][^】]+】`；④ 处理 N3（`round = current_round+1`）并确认 N4 字体环境。

> 附：AgentState 状态确认——`state.py:66-188` 全量字段中**无 `project_id`**（有 `project_contract: dict`、`requirements: dict` 但二者均无 `project_id` 键），故 M9 的 `project_contract.get("project_id","")` 必为空，仅当 `requirements.bid_number` 非空时契约近似成立，否则仍回退时间戳，契约未真正满足。
