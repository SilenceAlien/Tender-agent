# 修复记录 — 簇 C4 两处回归/误判

> 修复日期：2026-07-20
> 修复人：代码修复工程师（CodeBuddy Code）
> 仅改动文件：
> - `bid-agent/core/nodes/quality_checker.py`
> - `bid-agent/core/nodes/compliance_checker.py`
> 对照： `specs/code-audit/cluster_C4_v2.md`、 `system-workflow.md`（节点 ⑧、⑪）

## 1. N3（中）— 8000 字完整度门槛误伤格式固定短章

**问题**：上一轮 H2 修复在 `quality_checker.py` 对所有章节统一要求 ≥8000 字
（原 `:544-553`）。`ch2_authorization`（授权委托书）、`ch7_schedule`（进度安排）、
`ch8_after_sales`（售后服务）属规范明确的"格式固定型短章"，由模板确定性生成，
天然仅数百字，必触发完整度 FAIL → 路由 ⑨ → 重生成，形成死循环。

**修复**：
- 新增常量 `FORMAT_FIXED_SHORT_CHAPTERS = {"ch2_authorization","ch7_schedule","ch8_after_sales"}`
  （`quality_checker.py` 顶部，紧随 `BANNED_WORDS`）。
- 在字数门槛判定循环中加入豁免分支：章节 key 命中豁免集合时 `continue`，
  跳过 8000 字判定（仍保留 `【生成失败】` 占位跳过逻辑）。
- 豁免章节不再因此产生虚假完整度问题，避免无意义修订循环。

**验证**：单元推演——被豁免章节即使 <8000 字也不追加 `completeness_issues`，
verdict 不再因此 FAIL。

## 2. N2（中）— 保密泄露正则过宽误报 FAIL

**问题**：M5 修复的保密泄露正则（`compliance_checker.py` 原 `:182-198`）
`bank_account_pattern = r"\d{16,19}"`、`id_card_pattern = r"\d{17}[\dXx]"` 匹配任意
16–19 位连续数字（合同编号、项目编号等）；同时文本侧缺乏上下文区分，易把
"严格保密""遵守保密协议"等正面声明误判为违规 → high 级 → verdict FAIL 阻断管道。

**修复**（`compliance_checker.py` 第 5 项检查块）：
- **敏感标识符锚定**：手机号/身份证号/银行账号正则前加敏感上下文前缀
  `(?:手机|联系|银行|账号|卡号|身份证|证号|…)`，仅当真正带账号/卡号/手机等
  语境时才匹配，普通长数字串不再误报。
- **文本型泄露（新增）**：`(正面动词)?(泄露|披露|外泄…)[^。；;]{0,15}?(商业秘密|保密数据|机密|…)`。
  因 Python `re` 不支持变长后顾，改用可选捕获组判断前缀：前置
  `承诺|遵守|绝不|不泄露|未|没有` 等正面语境（group 命中）则**不判违规**；
  仅"实际泄露了第三方保密信息内容"才判 high 级违规。
- "我方承诺保密""遵守保密协议""绝不泄露客户商业秘密"等声明 → 合规（PASS）。

**验证**（正则实测）：
- `我方承诺严格保密，遵守保密协议` → 合规
- `我们绝不泄露客户的商业秘密` → 合规（正面承诺）
- `本项目严格保密` → 合规（无泄露动词）
- `我们泄露了XX公司的商业秘密` → 判违规
- `投标文件披露了客户的保密数据` → 判违规
- `合同编号1234567890123456` → 不误报（无敏感前缀）
- `账号：6222021234567890123` → 正确判违规

## 3. py_compile 结果

```
/Users/alanchris/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -m py_compile \
  bid-agent/core/nodes/quality_checker.py \
  bid-agent/core/nodes/compliance_checker.py
```
**结果：PY_COMPILE_OK（退出码 0，无语法错误）**。

## 4. 说明（未改动项）

本次仅按要求修复上述两处回归/误判，未触及 `cluster_C4_v2.md` 中其余未修复项
（M4/M6/L4/L6、N1/N2(中文分词)/N4/N5/N6），亦未改动其他节点文件。
