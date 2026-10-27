# 账票工作台领域定义

第一个验证用的工作台。设计目标：**用户全程零手填**，输入只有两种——平台导出的账单文件、随手拍的小票图片；输出是一份能对账的月度账本和一份可交付的成品报告。

## 数据表

### txn 交易流水

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 主键，取源平台交易号的哈希 |
| occurredAt | datetime | 交易发生时间 |
| source | enum | alipay / wechat / bank / receipt |
| counterparty | string | 交易对方原始文本 |
| rawDesc | string | 商品/备注原文，不做改写 |
| amountCents | int | 金额，单位分，恒为正 |
| direction | enum | out 支出 / in 收入 / neutral 不计收支（还款、提现、互转） |
| category | string | 分类，取值见 taxonomy |
| confidence | float | 分类置信度，来自规则命中或模型判断 |
| needsReview | bool | 低置信或金额异常时置真，进待确认队列 |
| evidenceIds | string[] | 关联凭证（小票/发票） |
| fingerprint | string | 去重键：时间＋对方＋金额＋方向 |

### merchant 商户档案

name、aliases[]、defaultCategory、firstSeen、lastSeen、txnCount。别名表是这套东西能越用越准的关键：同一商家在不同平台写法不同，命中一次即沉淀。

### taxonomy 分类表

餐饮、居住、交通、购物、日用、娱乐、医疗、教育、人情、订阅、转账还款、收入、其他。分类必须落在闭集里，避免模型每次自造新词导致月度无法对比。

### budget 预算

month、category、limitCents。首月无预算时只出结构分析，不出"超支"结论。

### evidence 凭证

id、path、kind（receipt/invoice/contract）、capturedAt、ocrText、linkedTxnIds[]、amountCents、merchant。

### report 报告成品

month、path、generatedAt、highlights[]。指向 deliverables 产出的文件，不存正文副本。

## 采集与入库规则

1. **格式识别优先于解析**：支付宝与微信导出的列名、编码、前置说明行都不同，先判类型再走对应解析，不允许"猜列序"。
2. **中性交易必须剔除**：还款、提现、账户互转、退款冲正若计入支出，月度总额直接失真，这是账本最典型的错法。
3. **去重按 fingerprint 而非交易号**：同一笔消费在支付宝账单和微信账单里可能各出现一次。
4. **分类先规则后模型**：命中 merchant 别名表直接定类（置信 1.0）；未命中才交模型判断，置信低于 0.7 一律进 needsReview，不静默归类。
5. **小票与流水双向挂接**：金额＋日期＋商户三项对上才算匹配，只对上两项进待确认，不做模糊自动合并。
6. **可对账**：任何统计口径都要能回到 txn 明细，报告里的每个数字必须可展开为具体条目。

## 验收口径

以脚本生成的 `_answer_key.json` 为唯一真值，验证下列各项：

- 入库笔数与总额与真值一致（分毫对齐，不允许四舍五入后相等）
- 收入/支出/中性三分类不错
- 分类准确率、needsReview 队列长度与人工复核量
- 跨期问答能引用到具体 txn（"哪三项涨最多"要给出金额、笔数、条目）
- 月度报告作为成品文件产出，且数字与明细自洽
- 全程用户手填次数为 0

## 待验证的风险

模型直接读几百行 CSV 做统计不可靠，正确姿势是用脚本算、模型只做归类判断与解释——这条链路里"谁算什么"的分工要在验证中确认，这决定工作台引擎的架构边界。
