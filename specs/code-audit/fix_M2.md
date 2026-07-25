# 修复记录：节点⑤ EligibilityChecker WARNING 语义（M2 / 审计 M3）

- 文件：`bid-agent/core/nodes/eligibility_checker.py`
- 规范依据：`system-workflow.md` 节点⑤「门禁路由(FAIL→终止, WARNING→继续, PASS→TemplateMatcher)」「硬资质证书匹配」
- 审计依据：`specs/code-audit/cluster_C2_v2.md` M3 段（WARNING 仅库空触发，缺「部分缺失有替代→WARNING」）

## 问题
原逻辑（旧 396-406 行）对任一缺失硬资质一律判 `FAIL` 终止；`WARNING` 仅在「企业资质库为空」时产生。规范⑤要求：缺失某项硬资质但库中提供可替代/等效资质时，应降级为 `WARNING` 并继续。可替代情形被错杀成 FAIL。

## 改法（仅修改本文件）

### 1. 新增替代项查询辅助函数（插在 `_qual_matches` 之后）
- `_CORE_SUFFIXES` / `_normalize_qual_deep(text)`：深层归一化，循环剥离 `认证/证书/资质/许可/许可证` 后缀（处理嵌套「认证证书」及「许可证」变体）。**仅用于替代判定**，不动原 `_qual_matches` 匹配路径。
- `_CODE_RE` / `_split_code_and_core(text)`：将归一化字符串拆为（前导标准号, 能力核心词），如 `ISO9001质量管理体系`→`('ISO9001','质量管理体系')`。
- `_find_qual_alternative(required, company_quals)`：保守判定——遍历库内证书，取深层归一化后的能力核心词，若与所缺项核心词互为子串且长度≥`_MIN_CORE_LEN`(4) 即判为可替代，返回该证书，否则 `None`。既覆盖 ISO9001↔GB/T19001（不同标准号、同能力域），也覆盖纯中文「人力资源服务许可证」↔「人力资源服务资质」（许可证/资质同核）。

### 2. 改写硬资质匹配与门禁路由（旧 385-426 行）
匹配循环改为三态收集：
- 命中 → `matched`
- 未命中但 `_find_qual_alternative` 命中 → `warning_quals`（有替代）
- 未命中且无替代 → `missing`（硬缺失）

verdict 判定保持门禁路由语义不变：
- 有 `missing`（无替代）→ `FAIL`（终止）
- 否则有 `warning_quals`（有替代）→ `WARNING`（继续）
- 否则 → `PASS`

报告新增 `warning_quals` 字段（原 `missing_quals` 仅含硬缺失项），`summary` 区分三种情况。

## 验证结果
- `py_compile` 通过，无语法错误。
- 临时构造 state 调节点逻辑（测后删除）：
  - 缺 `ISO9001质量管理体系认证`、库有 `GB/T19001质量管理体系认证` → **WARNING** ✓
  - 缺 `ISO27001...`、库有 `GB/T19001...` → **FAIL** ✓
  - 全命中 → **PASS** ✓
  - 缺 `人力资源服务许可证`、库有 `人力资源服务资质(甲级)` → **WARNING** ✓
  - 不相关证书（`ISO27001` vs `ISO9001` / `信息系统集成资质` vs `信息系统安全资质`）→ **FAIL**（不误判替代）✓
  - 软要求「具有独立法人资格」仍自动通过，混合替代场景 → **WARNING** ✓
  - 仅标准号无能力词（如 `ISO9001认证` vs `GB/T19001认证`）保守判 **FAIL**（无能力词无法证等价，符合保守要求）

## 是否引入新问题
- 未引入外部依赖。
- 未破坏「9 类软要求自动通过」「归一化去除后缀 + 子串模糊匹配」等既有正确逻辑（`_qual_matches`/`_normalize_qual` 原路径不变，深层归一化仅用于替代判定）。
- 门禁路由语义（FAIL→终止 / WARNING→继续 / PASS→继续）保持不变。
- 无新增问题。
