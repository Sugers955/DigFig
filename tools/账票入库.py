#!/usr/bin/env python3
"""账票工作台引擎（确定性部分）。

职责边界：本脚本只做格式识别、解析、去重、入库、算术与对账，不做任何语义判断。
需要模型参与的地方通过 `classify-apply` 回写，模型负责归类，脚本负责算数。

    python3 tools/账票入库.py ingest sample/*.csv          # 解析入库
    python3 tools/账票入库.py unclassified                  # 列出待模型归类的商户
    python3 tools/账票入库.py classify-apply decisions.json # 回写模型判断结果
    python3 tools/账票入库.py report --month 2026-03        # 统计与增量
    python3 tools/账票入库.py verify                        # 与 _answer_key.json 对账
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ledger.db"
ALIASES = ROOT / "workbench" / "ledger" / "merchants.json"

TAXONOMY = ["餐饮", "居住", "交通", "购物", "日用", "娱乐", "医疗", "教育",
            "人情", "订阅", "转账还款", "收入", "其他"]

# 中性交易特征：这些不是消费也不是收入，计入支出会让月度总额失真
NEUTRAL_HINTS = ("信用卡还款", "余额宝转出", "零钱提现", "好友转账", "转账-", "账户互转")
REFUND_HINTS = ("退款", "冲正", "已全额退款")

SCHEMA = """
CREATE TABLE IF NOT EXISTS txn (
  fp           TEXT PRIMARY KEY,
  source_id    TEXT,
  source       TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  counterparty TEXT NOT NULL,
  raw_desc     TEXT,
  amount_cents INTEGER NOT NULL,
  direction    TEXT NOT NULL,          -- out | in | neutral | refund
  category     TEXT,                   -- NULL 表示待模型归类
  confidence   REAL,
  classified_by TEXT,                  -- alias | model | manual
  needs_review INTEGER DEFAULT 0       -- 模型置信低于 0.7 时置 1，进待确认队列
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT, captured_at TEXT,
  amount_cents INTEGER, merchant TEXT, linked_fp TEXT, note TEXT
);
"""


def cents(text: str) -> int:
    return int((Decimal(str(text).replace("￥", "").replace(",", "").strip()) * 100).to_integral_value())


def open_db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_aliases() -> dict[str, str]:
    if ALIASES.exists():
        return json.loads(ALIASES.read_text(encoding="utf-8"))
    return {}


def fingerprint(source: str, when: str, party: str, amount: int, direction: str) -> str:
    """同一笔消费可能在两个平台各出现一次，去重按内容指纹而非平台交易号。"""
    key = f"{when[:10]}|{party}|{amount}|{direction}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def detect(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if "微信支付账单明细" in text or "交易类型" in text:
        return "wechat"
    if "交易订单号" in text or "导出信息" in text:
        return "alipay"
    return "unknown"


def parse_rows(path: Path) -> list[dict]:
    kind = detect(path)
    if kind == "unknown":
        raise SystemExit(f"无法识别账单格式：{path.name}（不允许猜列序）")
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header_at = next(i for i, ln in enumerate(lines) if ln.startswith("交易时间"))
    reader = csv.DictReader(lines[header_at:])
    out = []
    for row in reader:
        if not row or not (row.get("交易时间") or "").strip():
            continue
        party = (row.get("交易对方") or "").strip()
        desc = (row.get("商品说明") or row.get("商品") or "").strip()
        io = (row.get("收/支") or "").strip()
        amount = cents(row["金额(元)"].replace("￥", ""))
        direction = {"支出": "out", "收入": "in"}.get(io, "neutral")
        if any(h in party or h in desc for h in NEUTRAL_HINTS):
            direction = "neutral"
        elif any(h in desc or h in party for h in REFUND_HINTS):
            direction = "refund"
        out.append({
            "source": kind, "source_id": (row.get("交易订单号") or row.get("交易单号") or "").strip(),
            "occurred_at": row["交易时间"].strip(), "counterparty": party, "raw_desc": desc,
            "amount_cents": amount, "direction": direction,
            "fp": fingerprint(kind, row["交易时间"].strip(), party, amount, direction),
        })
    return out


def classify(party: str, aliases: dict[str, str]) -> tuple[str | None, float | None, str | None]:
    for alias, cat in aliases.items():
        if alias in party or party in alias:
            return cat, 1.0, "alias"
    return None, None, None


def cmd_ingest(args) -> None:
    con = open_db()
    aliases = load_aliases()
    seen, added, dup = 0, 0, 0
    for file in args.files:
        path = Path(file)
        rows = parse_rows(path)
        for r in rows:
            seen += 1
            cat, conf, by = classify(r["counterparty"], aliases)
            exists = con.execute("SELECT 1 FROM txn WHERE fp=?", (r["fp"],)).fetchone()
            if exists:
                dup += 1
                continue
            con.execute(
                "INSERT INTO txn (fp, source_id, source, occurred_at, counterparty, raw_desc,"
                " amount_cents, direction, category, confidence, classified_by)"
                " VALUES (:fp,:source_id,:source,:occurred_at,:counterparty,:raw_desc,"
                " :amount_cents,:direction,:category,:confidence,:by)",
                {**r, "category": cat, "confidence": conf, "by": by})
            added += 1
        print(f"解析 {path.name}: {len(rows)} 笔（识别为 {'微信' if detect(path)=='wechat' else '支付宝'}）")
    con.commit()
    todo = con.execute("SELECT COUNT(*) c FROM txn WHERE category IS NULL AND direction='out'").fetchone()["c"]
    print(f"合计读入 {seen} 笔，新增 {added} 笔，跨平台指纹去重 {dup} 笔")
    print(f"待归类支出条目 {todo} 条（别名表未命中，交模型判断）")
    con.close()


def cmd_unclassified(args) -> None:
    con = open_db()
    rows = con.execute(
        "SELECT counterparty, COUNT(*) n, SUM(amount_cents) c FROM txn"
        " WHERE category IS NULL AND direction='out' GROUP BY counterparty ORDER BY c DESC").fetchall()
    print(json.dumps([{"counterparty": r["counterparty"], "count": r["n"],
                       "amountYuan": round(r["c"] / 100, 2)} for r in rows],
                     ensure_ascii=False, indent=2))
    con.close()


def cmd_classify_apply(args) -> None:
    decisions = json.loads(Path(args.file).read_text(encoding="utf-8"))
    con = open_db()
    applied, rejected, review = 0, [], []
    for d in decisions:
        cat = d["category"]
        if cat not in TAXONOMY:
            rejected.append((d["counterparty"], f"分类 {cat} 不在闭集内"))
            continue
        conf = float(d.get("confidence", 0.9))
        cur = con.execute("UPDATE txn SET category=?, confidence=?, classified_by='model',"
                          " needs_review=? WHERE counterparty=? AND category IS NULL AND direction='out'",
                          (cat, conf, 1 if conf < 0.7 else 0, d["counterparty"]))
        applied += cur.rowcount
        if conf < 0.7:
            review.append((d["counterparty"], cat, conf))
        if d.get("persist_alias", True):
            aliases = load_aliases()
            aliases[d["counterparty"]] = cat
            ALIASES.parent.mkdir(parents=True, exist_ok=True)
            ALIASES.write_text(json.dumps(aliases, ensure_ascii=False, indent=2) + "\n", "utf-8")
    con.commit()
    left = con.execute("SELECT COUNT(*) c FROM txn WHERE category IS NULL AND direction='out'").fetchone()["c"]
    print(f"回写 {applied} 条归类判断，别名表已沉淀；仍待归类 {left} 条")
    for party, why in rejected:
        print(f"拒绝 {party}: {why}")
    for party, cat, conf in review:
        print(f"待人工确认（置信 {conf}，不静默采信）：{party} -> {cat}")
    con.close()


def month_of(row: sqlite3.Row) -> str:
    return row["occurred_at"][:7]


def cmd_report(args) -> None:
    con = open_db()
    month = args.month
    rows = con.execute("SELECT * FROM txn WHERE direction='out'").fetchall()
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_month: dict[str, dict] = defaultdict(lambda: {"total": 0, "count": 0})
    for r in rows:
        m = month_of(r)
        cat = r["category"] or "未归类"
        by_cat[cat][m] = by_cat[cat].get(m, 0) + r["amount_cents"]
        by_month[m]["total"] += r["amount_cents"]
        by_month[m]["count"] += 1
    prev = f"{int(month[:4])}-{int(month[5:])-1:02d}" if month[5:] != "01" else None
    print(f"=== {month} 支出 ===")
    print(f"总额 {by_month[month]['total']/100:.2f} 元，{by_month[month]['count']} 笔")
    if prev and prev in by_month:
        deltas = []
        for cat, per in by_cat.items():
            cur_v, prev_v = per.get(month, 0), per.get(prev, 0)
            if prev and prev_v == 0 and cur_v == 0:
                continue
            deltas.append((cat, cur_v, prev_v, cur_v - prev_v))
        deltas.sort(key=lambda x: -x[3])
        print(f"较 {prev} 增长最多的类目：")
        for cat, cur_v, prev_v, d in deltas[:3]:
            pct = f"{(cur_v - prev_v) * 100 / prev_v:+.1f}%" if prev_v else "新增"
            print(f"  {cat}: {cur_v/100:.2f} vs {prev_v/100:.2f} 元，{d/100:+.2f}（{pct}）")
        print(f"上月合计 {by_month[prev]['total']/100:.2f} 元 → 本月 {by_month[month]['total']/100:.2f} 元")
    print("分类构成：")
    for cat, per in sorted(by_cat.items(), key=lambda kv: -kv[1].get(month, 0)):
        v = per.get(month, 0)
        if v:
            ids = [r["fp"][:6] for r in con.execute(
                "SELECT fp FROM txn WHERE direction='out' AND (category=? OR (? IS NULL AND category IS NULL))"
                " AND substr(occurred_at,1,7)=? LIMIT 3", (cat, cat, month))]
            print(f"  {cat}: {v/100:.2f} 元（样例 {', '.join(ids)}）")
    con.close()


def cmd_verify(args) -> None:
    key = json.loads((ROOT / "sample" / "_answer_key.json").read_text(encoding="utf-8"))
    con = open_db()
    ok = True
    outs = con.execute("SELECT * FROM txn WHERE direction='out'").fetchall()
    got_count = len(outs)
    got_total = sum(r["amount_cents"] for r in outs)
    want_count = key["支出笔数合计"]
    want_total = sum(v["金额分"] for v in key["每月支出"].values())
    if got_count != want_count:
        ok = False
        print(f"FAIL 支出笔数 期望 {want_count} 实得 {got_count}")
    if got_total != want_total:
        ok = False
        print(f"FAIL 支出总额 期望 {want_total/100:.2f} 实得 {got_total/100:.2f}")
    per_month: dict[str, int] = defaultdict(int)
    for r in outs:
        per_month[month_of(r)] += r["amount_cents"]
    for m, v in key["每月支出"].items():
        if per_month.get(m, 0) != v["金额分"]:
            ok = False
            print(f"FAIL {m} 期望 {v['金额分']} 实得 {per_month.get(m, 0)}")
    per_cat: dict[str, int] = defaultdict(int)
    for r in outs:
        per_cat[f"{month_of(r)}|{r['category'] or '未归类'}"] += r["amount_cents"]
    mismatch = {k2: (v, per_cat.get(k2, 0)) for k2, v in key["每月每类支出"].items() if per_cat.get(k2, 0) != v}
    leaked = [r["source_id"] for r in con.execute(
        "SELECT source_id FROM txn WHERE direction IN ('out','in') AND (source_id LIKE '%REPAY%'"
        " OR source_id LIKE '%WITHDRAW%' OR source_id LIKE '%TRANSFER%' OR source_id LIKE '%REF%')")]
    if leaked:
        ok = False
        print(f"FAIL 中性交易漏进收支：{leaked}")
    if mismatch:
        ok = False
        print(f"FAIL 类目级不一致 {len(mismatch)} 项：{list(mismatch.items())[:5]}")
    total_rows = con.execute("SELECT COUNT(*) c FROM txn").fetchone()["c"]
    neutral_rows = con.execute(
        "SELECT COUNT(*) c FROM txn WHERE direction IN ('neutral','refund')").fetchone()["c"]
    pending = con.execute(
        "SELECT COUNT(*) c FROM txn WHERE category IS NULL AND direction='out'").fetchone()["c"]
    print(f"读入 {total_rows} 条，中性/退款 {neutral_rows} 条，待归类 {pending} 条")
    print("VERIFY:", "PASS 与真值分毫一致" if ok else "FAIL 见上")
    sys.exit(0 if ok else 1)


def cmd_evidence_apply(args) -> None:
    """把视觉模型读出的小票字段入账，并与流水双向挂接。

    挂接规则：金额、当日、商户三项全对才 linked；只对上两项进待确认，不做模糊自动合并。
    """
    items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    con = open_db()
    linked, pending, orphan = 0, 0, 0
    for it in items:
        amount = cents(it["amountYuan"])
        day = it["date"][:10]
        cands = con.execute(
            "SELECT fp, counterparty FROM txn WHERE direction='out' AND amount_cents=? AND substr(occurred_at,1,10)=?",
            (amount, day)).fetchall()
        hit = next((c for c in cands if it["merchant"] in c["counterparty"] or c["counterparty"] in it["merchant"]), None)
        if hit:
            fp, note = hit["fp"], "三项匹配"
            linked += 1
        elif cands:
            fp, note = cands[0]["fp"], "金额日期对上、商户不符，待确认"
            pending += 1
        else:
            fp, note = None, "无对应流水，可能是现金或未导出的支付渠道"
            orphan += 1
        con.execute("INSERT OR REPLACE INTO evidence (id, path, kind, captured_at, amount_cents,"
                    " merchant, linked_fp, note) VALUES (?,?,?,?,?,?,?,?)",
                    (it["id"], it.get("path", ""), "receipt", it["date"], amount, it["merchant"], fp, note))
    con.commit()
    print(f"凭证入账 {len(items)} 张：挂接 {linked} 张，待确认 {pending} 张，无对应流水 {orphan} 张")
    con.close()


def cmd_render_report(args) -> None:
    """把一个月渲染成可交付的成品文件；报告中每个数字都能回到 txn 明细。"""
    con = open_db()
    month = args.month
    outs = con.execute("SELECT * FROM txn WHERE direction='out' AND substr(occurred_at,1,7)=?",
                       (month,)).fetchall()
    total = sum(r["amount_cents"] for r in outs)
    income = con.execute("SELECT COALESCE(SUM(amount_cents),0) s FROM txn"
                         " WHERE direction='in' AND substr(occurred_at,1,7)=?", (month,)).fetchone()["s"]
    neutral_n = con.execute("SELECT COUNT(*) c FROM txn WHERE direction IN ('neutral','refund')"
                            " AND substr(occurred_at,1,7)=?", (month,)).fetchone()["c"]
    by_cat: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in outs:
        by_cat[r["category"] or "未归类"].append(r)
    prev_cat: dict[str, int] = defaultdict(int)
    if args.prev:
        for r in con.execute("SELECT * FROM txn WHERE direction='out' AND substr(occurred_at,1,7)=?",
                             (args.prev,)):
            prev_cat[r["category"] or "未归类"] += r["amount_cents"]

    lines = [f"# {month} 月度账本", "",
             f"支出合计 **{total/100:,.2f} 元**，共 {len(outs)} 笔；同期收入 {income/100:,.2f} 元。",
             f"中性交易与退款共 {neutral_n} 笔已剔除，不计入支出。", "",
             "## 分类构成", "", "| 分类 | 金额（元） | 笔数 | 占比 | 环比 |", "| --- | --- | --- | --- | --- |"]
    for cat, rows in sorted(by_cat.items(), key=lambda kv: -sum(r["amount_cents"] for r in kv[1])):
        v = sum(r["amount_cents"] for r in rows)
        pv = prev_cat.get(cat, 0)
        delta = "—" if not pv else f"{(v - pv) * 100 / pv:+.1f}%"
        lines.append(f"| {cat} | {v/100:,.2f} | {len(rows)} | {v * 100 / total:.1f}% | {delta} |")

    review = con.execute("SELECT counterparty, category, confidence FROM txn WHERE needs_review=1"
                         " GROUP BY counterparty").fetchall()
    ev_all = con.execute("SELECT COUNT(*) c FROM evidence").fetchone()["c"]
    ev_linked = con.execute("SELECT COUNT(*) c FROM evidence WHERE linked_fp IS NOT NULL").fetchone()["c"]
    lines += ["", "## 需要你看一眼的", ""]
    if review:
        lines += [f"- 商户「{r['counterparty']}」按 {r['category']} 入账，置信 {r['confidence']}，属低置信未经确认"
                  for r in review]
    else:
        lines.append("- 无低置信归类")
    lines.append(f"- 凭证 {ev_all} 张，其中 {ev_all - ev_linked} 张未挂接到流水")
    lines += ["", "## 可回溯性", "",
              f"库内共 {con.execute('SELECT COUNT(*) c FROM txn').fetchone()['c']} 条流水，"
              f"来源渠道 {', '.join(r['source'] for r in con.execute('SELECT DISTINCT source FROM txn'))}；"
              f"本报告每个分类金额均可按「月份+分类」回到明细表逐条核对。"]
    out_dir = ROOT / "workbench" / "ledger" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"月度账本_{month}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"成品报告已生成：{path.relative_to(ROOT)}（{path.stat().st_size} 字节）")
    con.close()


def main() -> None:
    p = argparse.ArgumentParser(description="账票工作台引擎")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("unclassified")
    s.set_defaults(fn=cmd_unclassified)
    s = sub.add_parser("classify-apply")
    s.add_argument("file")
    s.set_defaults(fn=cmd_classify_apply)
    s = sub.add_parser("report")
    s.add_argument("--month", required=True)
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("verify")
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("evidence-apply")
    s.add_argument("file")
    s.set_defaults(fn=cmd_evidence_apply)
    s = sub.add_parser("render-report")
    s.add_argument("--month", required=True)
    s.add_argument("--prev", default=None)
    s.set_defaults(fn=cmd_render_report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
