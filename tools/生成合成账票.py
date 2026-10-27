#!/usr/bin/env python3
"""生成账票工作台的合成测试数据：支付宝/微信账单 CSV、小票图片、对账真值。

数据全为虚构，仅用于验证采集入库链路，不含任何真实交易信息。
用法：python3 tools/生成合成账票.py
"""
from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

random.seed(20261027)

OUT = Path(__file__).resolve().parent.parent / "sample"
DAY_START, DAY_END = date(2026, 1, 1), date(2026, 3, 31)

# (商户, 分类, 单笔金额区间元, 每月笔数区间, 平台)
MERCHANTS = [
    ("美团外卖·张记麻辣香锅", "餐饮", (25, 68), (10, 16), "alipay"),
    ("瑞幸咖啡", "餐饮", (9.9, 29), (8, 14), "wechat"),
    ("全家便利店", "日用", (8, 45), (5, 9), "wechat"),
    ("盒马鲜生", "日用", (60, 260), (4, 7), "alipay"),
    ("滴滴出行", "交通", (12, 58), (6, 11), "wechat"),
    ("星巴克咖啡", "餐饮", (28, 62), (2, 5), "alipay"),
    ("京东商城", "购物", (39, 890), (2, 5), "alipay"),
    ("拼多多", "购物", (9, 120), (3, 8), "wechat"),
    ("腾讯视频VIP", "订阅", (25, 25), (1, 1), "wechat"),
    ("iCloud 存储空间", "订阅", (21, 21), (1, 1), "alipay"),
    ("三体书店", "教育", (50, 120), (1, 2), "wechat"),
    ("花礼网·生日鲜花", "人情", (120, 320), (1, 2), "wechat"),
    ("平安药房", "医疗", (18, 210), (1, 3), "alipay"),
    ("极客时间课程", "教育", (99, 399), (1, 2), "alipay"),
    ("海底捞火锅", "餐饮", (150, 420), (1, 2), "wechat"),
    ("中国石化", "交通", (200, 400), (1, 2), "alipay"),
]

TENANT = ("房东·王某某", "居住", (3600, 3600), (1, 1), "alipay")
SALARY = ("某某科技有限公司·工资", "收入", (16800, 16800), (1, 1), "bank")


def cents(text: str) -> int:
    return int(Decimal(text) * 100)


def yuan(c: int) -> str:
    return f"{Decimal(c) / 100:.2f}"


def rand_day(month: int) -> date:
    first = date(2026, month, 1)
    days = (date(2026, month + 1, 1) if month < 12 else date(2026, 12, 1)) - first
    d = first + timedelta(days=random.randrange(days.days))
    return d


def rand_time(d: date) -> str:
    return datetime(d.year, d.month, d.day, random.randrange(7, 23),
                    random.randrange(60), random.randrange(60)).strftime("%Y-%m-%d %H:%M:%S")


def build_rows() -> list[dict]:
    rows, seq = [], 0
    for month in (1, 2, 3):
        for name, cat, (lo, hi), (nlo, nhi), platform in [TENANT] + MERCHANTS:
            for _ in range(random.randint(nlo, nhi)):
                seq += 1
                amount = cents(f"{round(random.uniform(lo, hi), 2):.2f}")
                rows.append({
                    "id": f"D{month:02d}{seq:05d}",
                    "time": rand_time(rand_day(month)),
                    "counterparty": name,
                    "desc": f"{name}·消费" if cat != "居住" else "2026年3月房租",
                    "amount": amount,
                    "direction": "out",
                    "category": cat,
                    "platform": platform,
                })
        # 收入：工资
        rows.append({
            "id": f"D{month:02d}SAL",
            "time": rand_time(date(2026, month, 10)),
            "counterparty": SALARY[0],
            "desc": "代发工资",
            "amount": cents("16800.00"),
            "direction": "in",
            "category": "收入",
            "platform": "alipay",
        })
        # 中性交易：信用卡还款、提现、好友转账、退款冲正
        for tag, cp, amt in (
            ("repay", "信用卡还款", 420000),
            ("withdraw", "余额宝转出到银行卡", 200000),
            ("transfer", "好友转账-李", 15000),
        ):
            rows.append({
                "id": f"D{month:02d}{tag.upper()}",
                "time": rand_time(rand_day(month)),
                "counterparty": cp,
                "desc": cp,
                "amount": amt,
                "direction": "neutral",
                "category": "转账还款",
                "platform": "alipay",
            })
        # 退款（收入向，但不能算进收入）
        rows.append({
            "id": f"D{month:02d}REF",
            "time": rand_time(rand_day(month)),
            "counterparty": "京东商城",
            "desc": "退款-京东京造台灯",
            "amount": 19900,
            "direction": "refund",
            "category": "购物",
            "platform": "wechat",
        })
    rows.sort(key=lambda r: r["time"])
    return rows


def write_alipay(rows: list[dict]) -> None:
    mine = [r for r in rows if r["platform"] == "alipay"]
    with (OUT / "支付宝_2026年1-3月账单.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["导出信息：共 %d 笔，起始时间 2026-01-01 00:00:00" % len(mine)])
        w.writerow(["交易时间", "交易分类", "交易对方", "商品说明", "收/支", "金额(元)",
                    "支付方式", "交易状态", "交易订单号", "商家订单号", "备注"])
        for r in mine:
            io = {"out": "支出", "in": "收入", "neutral": "/", "refund": "/"}[r["direction"]]
            w.writerow([r["time"], r["category"], r["counterparty"], r["desc"], io,
                        yuan(r["amount"]), "余额宝", "交易成功", r["id"], f"M{r['id']}", ""])


def write_wechat(rows: list[dict]) -> None:
    mine = [r for r in rows if r["platform"] == "wechat"]
    with (OUT / "微信支付账单流水文件_2026年1-3月.csv").open("w", encoding="utf-8-sig", newline="") as f:
        f.write("-----------------------------------------------------------------\n")
        f.write("微信支付账单明细列表\n")
        f.write("起始时间：[2026-01-01 00:00:00]    终止时间：[2026-03-31 23:59:59]\n")
        f.write("-----------------------------------------------------------------\n")
        w = csv.writer(f)
        w.writerow(["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
                    "支付方式", "当前状态", "交易单号", "商户单号", "备注"])
        for r in mine:
            io = {"out": "支出", "in": "收入", "neutral": "/", "refund": "/"}[r["direction"]]
            w.writerow([r["time"], "商户消费", r["counterparty"], r["desc"], io,
                        yuan(r["amount"]), "零钱", "已收/付款", f"W{r['id']}", f"S{r['id']}", ""])


def draw_receipt(path: Path, title: str, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((p for p in [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ] if Path(p).exists()), None)
    big = ImageFont.truetype(font_path, 34, index=0)
    reg = ImageFont.truetype(font_path, 26, index=0)
    h = 90 + 44 * len(lines) + 60
    img = Image.new("RGB", (620, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 619, h - 1], outline=(190, 190, 190), width=2)
    d.text((40, 34), title, font=big, fill=(20, 20, 20))
    y = 100
    for line in lines:
        d.text((40, y), line, font=reg, fill=(30, 30, 30))
        y += 44
    d.text((40, y + 8), "合成票据 仅用于链路验证", font=reg, fill=(150, 150, 150))
    img.save(path)


def write_receipts(rows: list[dict]) -> None:
    rdir = OUT / "小票"
    rdir.mkdir(exist_ok=True)
    picked = [r for r in rows if r["direction"] == "out" and r["category"] in ("餐饮", "日用", "交通")]
    samples = random.sample(picked, 5)
    manifest = []
    for i, r in enumerate(samples, 1):
        name = {"餐饮": "餐饮小票", "日用": "便利店小票", "交通": "加油票据"}[r["category"]]
        p = rdir / f"receipt_{i:02d}_{r['id']}.png"
        draw_receipt(p, name, [
            f"商户：{r['counterparty']}",
            f"时间：{r['time']}",
            f"金额：￥{yuan(r['amount'])}",
            f"流水号：{r['id']}",
        ])
        manifest.append({"file": str(p.relative_to(OUT)), "txId": r["id"],
                         "amountCents": r["amount"], "merchant": r["counterparty"]})
    (rdir / "清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")


def write_answer_key(rows: list[dict]) -> None:
    out_total, out_count = defaultdict(int), defaultdict(int)
    cat_month = defaultdict(int)
    for r in rows:
        m = r["time"][:7]
        if r["direction"] == "out":
            out_total[m] += r["amount"]
            out_count[m] += 1
            cat_month[(m, r["category"])] += r["amount"]
    key = {
        "说明": "合成数据真值，用于校验工作台统计口径；金额单位分",
        "总笔数": len(rows),
        "支出笔数合计": sum(out_count.values()),
        "每月支出": {m: {"金额分": out_total[m], "笔数": out_count[m]} for m in sorted(out_total)},
        "每月每类支出": {f"{m}|{c}": v for (m, c), v in sorted(cat_month.items())},
        "中性交易": [r["id"] for r in rows if r["direction"] in ("neutral", "refund")],
        "分类闭集": sorted({r["category"] for r in rows}),
    }
    (OUT / "_answer_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(key["每月支出"], ensure_ascii=False))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = build_rows()
    write_alipay(rows)
    write_wechat(rows)
    write_receipts(rows)
    write_answer_key(rows)
    print(f"生成完成：{len(rows)} 笔交易 -> {OUT}")


if __name__ == "__main__":
    main()
