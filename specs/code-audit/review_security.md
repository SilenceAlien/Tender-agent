# 安全检查专项复审 — `bid-agent` 最近一轮修复

> 审查范围：fix_C2~C6 改动的 6 个文件（仅看变更部分）
> 审查项：路径遍历 / 文件名注入、敏感信息泄露、密钥处理、输入注入、文件读取路径约束、异常处理

---

## 一、真实安全/健壮性问题（置信度 ≥80）

### 🔴 [P1] 目录遍历 / 目录注入：`bid_subtype`、`bid_type` 未经清洗即拼为检索路径
- **位置**：`core/retrieval/reference_retriever.py:82`（`kb_type = "劳务外包类" if ("劳务" in bid_type) else bid_type`）、`:88`（`subtype_dir = self.kb_root / kb_type / bid_subtype / "chunks"`）、另 `:122` 邻居回退路径同类。
- **数据流（本轮新增，N4 修复引入）**：`info_verification_gate.apply_user_corrections`（`info_verification_gate.py:369`）把用户表单修正/确认的 `bid_subtype` 回写顶层 `state["bid_subtype"]`；下游 `retrieve_for_section(bid_subtype=...)` → `_find_reference_files(bid_subtype=...)` 直接将其作为目录名拼入路径。
- **利用**：`corrected_fields={"bid_subtype": "../../etc/passwd"}` 或 `bid_type="../foo"` →
  `kb_root / "劳务外包类" / "../../etc/passwd" / "chunks"`，若目标目录存在 `.txt` 则被 `glob("*.txt")` 命中并经 `_extract_best_snippet` 读取，内容作为"参考范文"注入生成 prompt → **目录外任意 .txt 内容泄露 + 标书内容注入**。
- **置信度**：**85**（确为真实；需攻击者能操控 `bid_subtype`/`bid_type`，在 GUI/API 场景可达；即便 GUI 用下拉，函数签名接受任意 dict，须按不可信输入对待）。
- **修复建议**：
  1. 对 `bid_subtype`、`bid_type` 做字符白名单清洗：`re.sub(r'[^\w\u4e00-\u9fff\-]', '_', str(v))`，并拒绝空值/含 `..`/绝对路径。
  2. 解析后强制约束在 `kb_root` 内：`(self.kb_root / kb_type / bid_subtype / "chunks").resolve()` 须 `is_relative_to(self.kb_root.resolve())`，否则跳过。
  3. 校验 `bid_subtype` 为 `str` 类型，避免非字符串触发 `Path` 构造 TypeError 击穿检索。

---

## 二、各检查项结论

1. **路径遍历 / 文件名注入（doc_assembler 导出文件名）**：**无虞**。
   `doc_assembler.py:1567` `safe_id = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', str(project_id)).strip('_')[:50]` 已剥离 `/ \ .` 及 `..`，`export_path = Path(export_dir)/filename` 中文件名组件无法逃逸 `export_dir`；回退链与 `max(1, int(round))` 防 `v0` 均合理。无路径遍历风险。

2. **敏感信息泄露（compliance_checker PII 正则）**：**无虞**。
   `compliance_checker.py:196-230` 命中后仅记录 `f"检测到 {len(matches)} 处疑似{pat_name}..."` 计数与通用提示，未将手机号/身份证号/账号等命中原文写入 `legal_issues` 或日志；文本型泄露分支（`:224`）仅判违规不写内容。敏感前缀锚定 + 正面语境排除降低了误报。`leak_pattern` 误判最坏仅导致错误 FAIL（健壮性问题，非外泄）。

3. **密钥 / 凭证处理（MockEmbedder / OPENAI_API_KEY）**：**无虞**。
   `reference_retriever.py` 全程未打印 `OPENAI_API_KEY`；`__init__`（`:58-61`）注释明确 MockEmbedder 离线/测试专用、缺 key 仍可运行，语义路径退化为随机向量；检索日志仅含文档字符数与子类型权重（`:116`），无凭证泄露。

4. **输入注入（模板 .format / apply_user_corrections）**：**基本无虞（1 处残留见附录）**。
   变更内 `doc_assembler` 文件名（已清洗）、PAGEREF 书签名（`_bidsect_{i}`，i 为 int）均不可被用户输入篡改；`header/footer` 文本仅作纯文本 run 写入 DOCX，无代码执行点。`apply_user_corrections` 注入值最终进入路径（见问题一，已单列）与正文纯文本，无 f-string/format 注入。

5. **文件读取路径（reference_retriever._load_doc_by_id）**：**残留风险（<80，见附录）**。
   `:160-177` `self.kb_root / doc_id` 与 `rglob(doc_id)` **无白名单/目录约束**，若 `doc_id`（FAISS 检索结果）含 `../` 或绝对路径可越出知识库。但 `doc_id` 来自内部 FAISS 索引、非直接用户前端输入，本轮亦未改动此逻辑 → 置信度 70，**不计入本次 ≥80 结论**，建议下轮补 `resolve().is_relative_to(kb_root)` 约束。

6. **异常处理**：**无虞**。
   各 `except` 均带具体类型或 `except Exception as e` 并 `logger.warning/error`；无裸 `except:` 吞掉关键异常。`doc_assembler` 的 PAGE/书签 OXML 操作（`:417/:445`）在 `_build_docx` 外层 `try/except`（`:1573`）包裹，单页域失败不致静默丢失整文档，仅返回 FAILED。`_find_reference_files` 内 `except Exception:`（`:104`/`:128`）仅跳过可选共享/近邻检索，属预期降级。

---

## 三、附录（置信度 <80，供后续优化，不计入本轮 ≥80）

- **A. `_load_doc_by_id` 缺目录约束**（置信度 70，前述第 5 项）：补 `is_relative_to` 白名单。
- **B. `quality_checker.py:476` `.format` 注入**（置信度 55，预现存、非本轮变更）：`_QUALITY_CHECK_PROMPT.format(sections_context=...)` 的 `sections_context` 含生成章节原文，若含 ASCII `{`/`}` 会抛 `KeyError`；虽被外层 `try/except`（`:581`）捕获降级为跳过 LLM 质检，但可能静默丢失深度质检。建议改用 `str.format_map` 或先转义 `{`/`}`。
- **C. 裸 `except Exception:`（置信度 40）**：`reference_retriever.py:104`、`info_verification_gate.py:199` 静默 `pass` 仅跳过可选特性，影响有限，可补 `logger.debug` 便于排查。

---

## 四、结论
本轮修复在文件名清洗、PII 不落盘、密钥不打印、异常处理上均**安全无虞**；唯一 ≥80 真实问题是 **`bid_subtype`/`bid_type` 作为目录路径组件未做清洗与目录约束（路径/目录遍历，reference_retriever.py:82/88）**，由 N4 修复新增的数据流引入，建议尽快加字符白名单 + `is_relative_to` 约束。
