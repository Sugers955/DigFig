# DigFig

面向企业办公与个人效率场景的 **AI 数字员工 / 工作台引擎**。以「岗位」和「工作台」为单位交付能力：把可标准化的重复工作交给 AI 执行，人负责定义规则、把住关口、处理例外。

引擎跑在 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（dsh）之上，复用它的会话、工具调用、文件读写、定时任务、知识检索与成品交付能力；仓库里承载的是**工作台定义**——数据表、判定规则、交付物模板与领域指令包。

## 设计取向

市面上的"AI 工作台"大多停在页面层：模块清单、图表、录入表单，用户每天自己往里填数据，填不动就流失。本项目的判断是，工作台的难点不在界面而在**录入、算账、交付**这三件事能不能不用人做，因此分工固定：

- **确定性脚本**负责格式识别、解析、去重、算术、对账——这些不允许模型代劳，因为金额与笔数必须分毫不差。
- **模型**只负责没命中规则表时的语义判断（归类、抽取、解释），且低置信结论一律进待确认队列，不静默采信。
- **交付物**是工作台存在的理由：月度账本、成长册、对账单，每个数字都能回到明细逐条核对。

入选工作台范围的硬性标准：输入天然非结构化且量大、跨期结论真有用、判断对错有硬锚点、主链路不依赖外部账号授权。

## 仓库结构

```
profiles/       dsh profile：bundle 顺序与模型通道 patch
workbench/      工作台定义（数据表、规则、领域指令、判定结果）
tools/          引擎脚本与测试数据生成器
sample/         合成账单与小票，不含任何真实交易
data/           运行期数据（SQLite，不入库）
```

## 内置工作台

### 账票与预算

输入：支付宝、微信导出账单 CSV 与小票图片。输出：月度账本成品报告。

处理链路为格式识别 → 解析入库 → 指纹去重 → 商户别名命中定类 → 未命中条目交模型归类 → 低置信进待确认 → 与真值对账 → 渲染成品。四条硬规则：**中性交易（还款、提现、互转、退款冲正）必须剔除**，否则月度支出失真；**去重按内容指纹而非平台交易号**，同一笔消费可能在两个平台各出现一次；**分类必须落在闭集内**，避免模型每次自造新词导致月度不可比；**小票挂接需金额、日期、商户三项全对**，只对上两项进待确认。

```bash
python3 tools/生成合成账票.py                    # 造一批可核对的测试数据
python3 tools/账票入库.py ingest sample/*.csv    # 解析入库，列出待归类
python3 tools/账票入库.py unclassified           # 交给模型判断的清单
python3 tools/账票入库.py classify-apply workbench/ledger/decisions.json
python3 tools/账票入库.py evidence-apply workbench/ledger/receipts-read.json
python3 tools/账票入库.py report --month 2026-03
python3 tools/账票入库.py render-report --month 2026-03 --prev 2026-02
python3 tools/账票入库.py verify                 # 与 sample/_answer_key.json 对账
```

`verify` 是这套引擎的验收闸门：笔数、月度金额、类目级金额、中性交易是否漏入收支，任一项不符即退出码非零。

## 环境要求

- dsh 运行时随 DeepSeek Harness 发行（内置 Node 24、pnpm 11、Python 3.12 与预编译原生模块），原生模块按平台预编译，不要求使用者自行编译
- Python 3.12+（引擎脚本，仅用标准库与 Pillow）
- SQLite（引擎落库，零外部服务依赖）

## 模型通道

`profiles/digfig/cordis.patch.yml` 声明 provider 与 model。当前验证使用 `workbuddy` 通道（复用本机 WorkBuddy 桌面端登录态，国内版与国际版为两个独立 provider，`deepseek-v4.1-flash` 等型号由在线目录刷新）。换官方通道只需替换 provider 与 `DEEPSEEK_API_KEY`，引擎与工作台定义无需改动。

## 开发指南

- `main` 分支保持可发布状态，功能开发走短分支加 PR 评审。
- 提交信息沿用 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:`）。
- 新增工作台时，先写 `workbench/<name>/domain.md` 的表结构与判定规则，再实现引擎脚本，最后补真值文件与 `verify`；三者缺一不进入发布列表。
- 涉及数据外发、删除、提交的工具，必须保留人工确认开关。

## 许可证

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE)（SPDX：`PolyForm-Noncommercial-1.0.0`）：可自由阅读、学习、修改，并在非商业场景下使用与分发；以商业目的使用、部署或提供服务需事先取得授权，版权方保留另行授予商业许可的权利。

Required Notice: Copyright 2026 Sugers955. 保留所有权利。
