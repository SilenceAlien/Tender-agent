# 簇 C5 — 审计发现

> 审计对象：`bid-agent/core/nodes/doc_assembler.py`（1490 行）
> 对照规范：`system-workflow.md` → 节点 ⑭ DocumentAssembler（第 496–535 行）
> 审计方式：完整逐行阅读源码 + 逐条规范对照

---

## 1. 文件清单（路径+行数+职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/doc_assembler.py` | 1490 | DocumentAssembler 节点：将 `sections` 与 `format_rules` 渲染为格式化 DOCX（覆盖页/目录/章节/Markdown/Mermaid/占位符/签章页）；并暴露 LangGraph 节点入口 `doc_assembler()` |

辅助函数分布：
- 页边距/工具：`_format_margin`(37) `_cm_to_emu`(62) `_display_width`(67) `_wrap_label`(72) `_clean_mermaid_label`(117)
- Mermaid：`_parse_mermaid_flowchart`(132) `_mermaid_to_text_diagram`(244) `_mermaid_to_png`(309) `_mermaid_to_png_matplotlib`(329) `_mermaid_to_png_pillow`(607)
- Markdown：`_is_markdown_table_line`(815) `_is_separator_line`(821) `_add_markdown_heading`(838) `_add_markdown_list_item`(876) `_parse_markdown_table_row`(917) `_add_table_to_docx`(944) `_add_runs_with_placeholders`(1001)
- 装配：`_build_docx`(1081) `doc_assembler`(1433)

---

## 2. 节点实现对照表

| 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|---------|---------------------|------|------|
| 字体 正文仿宋_GB2312、标题黑体/楷体 | :26-27, :1118-1125, :855 | ✓ | DEFAULT 取"正文仿宋_GB2312"映射为"仿宋_GB2312"；标题 `level<=2→黑体` 否则 `楷体`；与规范一致 |
| 字号 正文小四 12pt、标题 16→14→13→12 | :1127, :856, :1212 | ✓ | Normal 12pt；size_map{1:16,2:14,3:13,4:12,5:12,6:12}；章节标题 16pt |
| 页边距 默认上下3.7/左右2.8 | :26, :44-45, :1108-1113 | **错误** | 代码默认 `上3.7/下3.5/左2.8/右2.6`，与规范默认 `上下3.7/左右2.8` 不一致（下3.5≠3.7、右2.6≠2.8） |
| 行距 默认 28 磅固定 | :1141-1148 | ✓ | `pf.line_spacing = Pt(28)`（固定值），与规范一致 |
| 目录 自动生成（Tab 前导符+页码） | :1172-1192 | **错误** | 仅生成装饰性目录：Tab+点前导符(✓)，但页码硬编码为 `"第{i}页"`(:1189)，并非真实页码，也未插入 Word 自动目录域（不随内容更新） |
| 页码 连续页码 | 全文 | **缺失** | 全文件无任何页脚/页眉页码域代码，连续页码完全未实现 |
| 页眉页脚 支持自定义 | 全文 | **缺失** | 未读取/未渲染 `format_rules` 中的页眉页脚配置，文档无 header/footer |
| Markdown 标题 ##黑体14 / ###楷体13 | :838-874, :855-856 | ✓ | `level<=2→黑体14`、否则`楷体13`，与规范一致 |
| Markdown 表格→Word 表格（自动列宽） | :944-998 | **需人工确认** | 使用 `add_table`+`Table Grid` 样式，未显式调用 autofit/allow_autofit，是否真正"自动列宽"需打开 Word 验证 |
| Markdown 列表 有序/无序 | :876-914, :893-896 | ✓ | `-`/`*`→`•`、数字→`N. `，正确渲染 |
| Markdown 加粗 **text** | :1001-1075, :1039-1046 | ✓ | `**…**` 拆段加粗，正确 |
| Mermaid 检测 ```mermaid | :1221-1223 | ✓ | `mermaid_block_re` 正确识别代码块 |
| Mermaid 用 matplotlib 渲染为图片 | :309-326, :329-604 | ✓ | 主用 matplotlib 后端（Pillow 兜底），插入 DOCX |
| Mermaid 力导向布局+箭头+边标签 | :383-404（布局）, :517-545（边） | **错误** | 规范要求"力导向算法计算节点位置"，代码实际为**树形/DFS 递归布局**(`_assign`)，非力导向；箭头(✓)、边标签(✓)已实现 |
| 占位符 【待填写：...】→红色加粗 | :1062-1065 | ✓ | 占位符段 `bold=True`+`RGBColor(0xFF,0,0)` 红色，正确 |
| 占位符正则 【待填写[：:][^】]+】 | :1220 | **错误** | 代码正则 `(【待填写[^】]*】)` **未强制冒号**且允许空内容（`*` 而非 `+`），比规范宽松，会匹配"【待填写】""【待填写xxx】"等无冒号形式 |
| 签章页 最后自动添加显示 seal_requirement | :1409-1415 | ✓ | `add_page_break` 后插入 `（{seal_requirement}）`，正确 |
| 输出 export_path(DOCX) | :1433-1490, 文件名:1465-1469 | **错误** | docstring 契约要求 `data/exports/{project_id}_标书_v{round}.docx`（:11），实际文件名用时间戳 `{timestamp}_标书_v{round}.docx`（:1468），**未含 project_id** |

---

## 3. 错误/缺陷明细（高/中/低）

### 高（High）
1. **连续页码缺失** — 全文件无页脚页码域代码。规范明确要求"连续页码"。`doc_assembler.py` 通篇未调用 `section.footer.paragraphs` 或 `add_field(W numpages/page)`。
2. **页眉页脚自定义缺失** — 规范"支持自定义"页眉页脚，代码未读取 `format_rules` 中相关字段，文档无任何 header/footer 段落。
3. **Mermaid 布局算法错误** — 规范要求"力导向算法"，代码 `:383-404` 实现的是树形递归布局（`_assign`：叶子依次排 x、父节点取子节点均值），对含环/非树状图会退化为孤立节点任意摆放，与"力导向"不符。
4. **目录页码造假** — `:1189` 将页码写为固定的 `"第{i}页"`，假设每章恰好一页；实际章节多页，页码全部错误，且未使用 Word 自动目录域（打开后不会自动更新）。

### 中（Medium）
5. **页边距默认值与规范冲突** — `DEFAULT_FORMAT`(:26) 为 `下3.5/右2.6`，规范默认 `上下3.7/左右2.8`。即便按规范"默认"也应是 3.7/2.8。注：文件自身 docstring(:9) 也写 `下3.5/右2.6`，与 system-workflow 自相矛盾。
6. **导出文件名不含 project_id** — `:1468` 用时间戳命名，违背节点契约 `:11` 的 `{project_id}_标书_v{round}.docx`。`project_id` 未从 state 取出（state 中亦无该字段，见需人工确认项）。
7. **占位符正则与规范不符** — `:1220` `(【待填写[^】]*】)` 不要求冒号、`*` 允许空内容；规范正则为 `【待填写[：:][^】]+】`（必须有冒号、至少 1 字符）。会误匹配无冒号占位符。

### 低（Low）
8. **表格单元格字号 10.5pt** — `:998` 表格内文字固定 10.5pt，规范未规定表格字号，"自动列宽"也未显式启用（见对照表"需人工确认"）。
9. **正则每次循环重编译** — `placeholder_re`(:1220)、`heading_re`/`list_re`(:1339-1340) 在 `for section` 循环体内重复 `re.compile`，性能冗余（非功能性）。

---

## 4. 遗漏功能（规范有、代码无）

1. **连续页码** —— 规范"页码：连续页码"，代码零实现。
2. **页眉页脚自定义** —— 规范"页眉页脚：支持自定义"，代码无 header/footer 渲染逻辑，亦不读取 `format_rules` 中页眉页脚配置。
3. **真实自动目录** —— 规范"目录：自动生成（Tab 前导符+页码）"；代码仅生成静态占位目录，非 Word 域，页码为伪造。
4. **Mermaid 力导向布局** —— 规范明确"力导向算法计算节点位置"，代码为树形布局。
5. **项目级文件名（project_id）** —— 节点契约要求文件名含 `project_id`，代码未实现。

---

## 5. 跨节点/横切问题

1. **规范内部不一致**：`doc_assembler.py` 文件 docstring(:9) 写页边距 `下3.5/右2.6`，而 `system-workflow.md` 节点 ⑭(:511) 写默认 `上下3.7/左右2.8`。两处规范对同一参数给出不同默认值，需先统一规范再修代码。
2. **AgentState 字段缺口**：中文规范 ⑭ 输入为 `sections` + `requirements.format_rules`；代码 `:1447` 正确取 `requirements.format_rules`。但导出文件名所需的 `project_id` 在 `system-workflow.md` 第八节 AgentState 字段表(:692-715)中**并不存在**（无 `project_id` 字段），故代码无法按契约填充——属规范与状态定义脱节。
3. **占位符口径未对齐**：生成侧（节点 ⑦ 防虚构指令 :304）要求用 `【待填写：具体说明】`（带冒号），而装配侧正则(:1220)放宽为可不带冒号；若将就宽松正则，与 SectionGenerator 约定格式不一致时可能漏标红。

---

## 6. 需人工确认项

1. **表格"自动列宽"是否生效** —— `:944-998` 仅设 `Table Grid` 样式、未显式 `table.autofit`/`allow_autofit`，需打开生成的 DOCX 确认列宽是否自动分配（python-docx 默认表格布局依赖样式，行为待验证）。
2. **Mermaid 节点 ID 仅 `[A-Za-z]\w*`** —— `_parse_mermaid_flowchart`(:147-155) 要求节点 ID 以字母开头；若 SectionGenerator 产出含中文 ID（如 `节点1[...]`）的图表，解析将失败并退化为文本树，需确认生成内容是否含中文节点 ID。
3. **`project_id` 来源** —— 规范契约要求文件名含 `project_id`，但 AgentState 字段表无此字段；需确认应从 `requirements.project_name`/`bid_number` 派生，还是补充 state 字段。
4. **`doc_assembler(state, export_dir=None)` 签名** —— 节点契约(:4)为单参 `doc_assembler(state)`，实际多一可选 `export_dir`；在 LangGraph `add_node` 注入 kwargs 场景下是否影响调用需确认。
5. **封面/日期页** —— 规范未要求封面，代码(:1156-1170)自行添加"投标书"+日期封面页，属合理扩展但超出规范描述，需确认是否期望行为。
6. **图片段落行距覆盖** —— `:1271` 将图片段落 `line_spacing=1.0` 覆盖默认 28pt 固定值，是否影响"全篇 28 磅固定行距"一致性需确认。
