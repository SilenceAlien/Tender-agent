# 修复记录 — 簇 C5（doc_assembler.py 节点 ⑭）

> 修复文件：`bid-agent/core/nodes/doc_assembler.py`（唯一修改文件）
> 对照：specs/code-audit/cluster_C5_v2.md、system-workflow.md 节点 ⑭
> 验证命令：`python3 -m py_compile bid-agent/core/nodes/doc_assembler.py` → 通过（PY_COMPILE_OK）

## 1. H7：装配格式（政府采购硬要求）

### 1.1 连续页码（PAGE 域）
- 位置：`doc_assembler.py:1172-1192`（页眉页脚 section 循环）
- 改法：取消原注释掉的页眉页脚代码；每个 section 的 footer 段落调用新增 `_add_field(fpara, "PAGE", "1")`，以 OXML `w:fldChar`+`w:instrText` 插入连续 PAGE 域，居中显示自动连续页码。
- 新增辅助函数：`_add_field`（`:417`）用于通用 Word 域插入。

### 1.2 页眉页脚自定义
- 位置：`doc_assembler.py:1172-1191`
- 改法：读取 `format_rules.get("header")`/`format_rules.get("footer")`；非空时分别写入 `section.header` / `section.footer` 段落（页脚含自定义文本 + 连续页码域）。无则留空。

### 1.3 Mermaid 力导向布局
- 位置：`doc_assembler.py:502`（matplotlib 渲染器）、`:714`（Pillow 渲染器）
- 改法：删除两个渲染器内的分层树布局（`_assign`/roots/leaf_x），统一改调新增的 `_force_directed_layout`（`:334`）。该函数实现 Fruchterman–Reingold 力模拟（节点斥力 + 边弹簧引力 + 冷却退火），迭代 400 步后归一化坐标，满足规范「力导向算法计算节点位置」；箭头与边标签渲染逻辑保持不变。

### 1.4 真实目录页码（PAGEREF 域）
- 位置：`doc_assembler.py:1262-1277`（目录生成）、`:1287-1303`（章节标题书签）
- 改法：删除硬编码 `f"{section_name}\t第{i}页"`；改为为每个章节生成确定书签名 `_bidsect_{i}`，目录行用 `_add_field(..., f"PAGEREF {bm_name} \h", "1")` 插入 PAGEREF 域；并在对应章节标题段落通过新增 `_add_bookmark`（`:445`）插入 `bookmarkStart`/`bookmarkEnd`，使 Word 打开更新域后得到真实页码。

## 2. M9 + N3：文件名契约
- 位置：`doc_assembler.py:1559-1571`（doc_assembler 函数内）
- 改法：删除时间戳回退；`project_id` 回退链为 `project_contract.project_id → requirements.bid_number → project_contract.bidder_name → project_contract.project_location → "标书"`；`round = max(1, int(current_round))` 避免首轮 `v0`；文件名固定 `{safe_id}_标书_v{round}.docx`，并去非法字符（`[^\w\u4e00-\u9fff\-]`→`_`，截断 50，去首尾下划线）。

## 3. M10：占位符正则
- 位置：`doc_assembler.py:1308`
- 改法：正则由 `(【待填写[^】]*】)` 收紧为 `(【待填写[：:][^】]+】)`——强制冒号且冒号后至少一个非】字符；红色加粗渲染逻辑不变。

## 4. 验证结果
- `py_compile`：**通过**（退出码 0，无语法错误）。
- 模块导入测试（stub core.state）：`MODULE_IMPORT_OK`。
- 力导向单测：4 节点输入产出 4 个互异坐标，正常。
- M10 正则单测：`【待填写：资质】` 匹配；`【待填写】`（空）、`【待填写内容】`（无冒号）均不匹配。
- 回归校验：Markdown→Word（标题/表格/列表/加粗）、占位符红粗、图片插入与缩放、签章页等逻辑均未改动，保持原样。

## 5. 未改动 / 说明
- `_mermaid_to_text_diagram` 文本兜底仍为树形（仅 PNG 渲染失败兜底用，规范主要针对图片渲染，已满足）。
- 连续页码对封面/目录页未强制跳过（规范「可选跳过」，保留连续页码，符合要求）。
- 该 Python 解释器 site-packages 存在损坏的 `docx.py` 占位文件，导致无法在此环境端到端生成 DOCX；但 `py_compile`、模块导入与纯函数单测均通过，Word 域/书签构造为标准 python-docx OXML 写法。
