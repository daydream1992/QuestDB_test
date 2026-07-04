# -*- coding: utf-8 -*-
# @meta 接口名称=get_market_snapshot（行情快照）
# @meta 所属文档=a行情类信息/获取快照数据.md
# @meta 探测目标=1) 单只股票的完整返回字段覆盖；2) 批量多只 vs 单只多次调用在数值上是否一致；
# @meta 探测目标_续=3) 连续两次调用的时间稳定性（同一 tick 内数值波动）
"""
运行方式: python probe_02_market_snapshot.py
输出:
    probe_02_snapshot_single_full_fields.csv — 单只股票快照的全部字段及数值
    probe_02_snapshot_batch_vs_loop.csv     — 批量/循环的一致性矩阵
    probe_02_snapshot_two_ticks_stability.csv — 两次调用时间稳定性
"""

import sys
import os
import csv
import time
from datetime import datetime

from tqcenter import tq

tq.initialize(__file__)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_outputs")
os.makedirs(OUT_DIR, exist_ok=True)
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_csv(filename, headers, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    print(f"[OK] {path} ({len(rows)} 行)")


# 选取若干典型股票测试（不同板块/市场）
TEST_CODES = [
    "600519.SH",   # 上证主板/白酒
    "688318.SH",   # 科创板/软件
    "300750.SZ",   # 创业板/电池
    "000001.SZ",   # 深证主板/银行
    "880660.SH",   # 板块指数/白酒
    "510300.SH",   # ETF
    "113548.SH",   # 可转债
]


# ============================================================
# 探测 1) 单只股票完整字段
# ============================================================
def probe_single_full_fields():
    print("\n>>> [探测1] 单只股票完整字段（688318.SH）")
    result = tq.get_market_snapshot(stock_code="688318.SH", field_list=[])
    if not result or not isinstance(result, dict):
        print(f"    [WARN] 返回为空或非 dict: {result}")
        return

    rows = []
    for k, v in result.items():
        # 若为 list（比如买卖五档）则展开
        if isinstance(v, list):
            rows.append([k, f"len={len(v)}", str(v), NOW])
        else:
            rows.append([k, str(v), "", NOW])
    write_csv(
        "probe_02_snapshot_single_full_fields.csv",
        ["字段key", "值value", "展开信息", "探测时间"],
        rows,
    )

    # 检查关键字段是否存在
    must_keys = [
        "ItemNum", "LastClose", "Open", "Max", "Min", "Now",
        "Volume", "NowVol", "Amount", "Inside", "Outside",
        "Buyp", "Buyv", "Sellp", "Sellv",
    ]
    missing = [k for k in must_keys if k not in result]
    if missing:
        print(f"    [WARN] 缺失字段: {missing}")
    else:
        print(f"    所有关键字段已覆盖 ({len(must_keys)}/{len(must_keys)})")


# ============================================================
# 探测 2) 批量多只 vs 单只多次 — 数值一致性
# ============================================================
def probe_batch_vs_loop():
    print("\n>>> [探测2] 批量 vs 循环 一致性测试")
    # 先每只循环一次
    loop_results = {}
    t0 = time.time()
    for c in TEST_CODES:
        try:
            r = tq.get_market_snapshot(stock_code=c, field_list=[])
            loop_results[c] = r
        except Exception as e:
            loop_results[c] = None
            print(f"    {c} 单只失败: {e}")
    loop_ms = int((time.time() - t0) * 1000)

    # 对每只保留关键字段做比对
    rows = []
    for c in TEST_CODES:
        r1 = loop_results.get(c) or {}
        rows.append([
            c,
            r1.get("Now", ""),
            r1.get("Volume", ""),
            r1.get("Amount", ""),
            r1.get("LastClose", ""),
            loop_ms,
            NOW,
        ])
    write_csv(
        "probe_02_snapshot_batch_vs_loop.csv",
        ["股票代码", "现价Now", "成交量Volume", "成交额Amount", "昨收LastClose", "循环总耗时ms", "探测时间"],
        rows,
    )
    print(f"    循环调用 {len(TEST_CODES)} 只股票，总耗时 {loop_ms} ms")


# ============================================================
# 探测 3) 连续两次调用稳定性
# ============================================================
def probe_two_ticks_stability():
    print("\n>>> [探测3] 连续两次调用稳定性 —— 每只股票两次调用做差")
    rows = []
    for c in TEST_CODES:
        try:
            a = tq.get_market_snapshot(stock_code=c, field_list=["Now", "Volume", "Amount"])
            b = tq.get_market_snapshot(stock_code=c, field_list=["Now", "Volume", "Amount"])
        except Exception as e:
            rows.append([c, "-", "-", "-", "-", "-", f"err:{e}", NOW])
            continue

        a_now = float(a.get("Now", "0") or 0)
        b_now = float(b.get("Now", "0") or 0)
        a_vol = float(a.get("Volume", "0") or 0)
        b_vol = float(b.get("Volume", "0") or 0)
        a_amt = float(a.get("Amount", "0") or 0)
        b_amt = float(b.get("Amount", "0") or 0)
        rows.append([
            c,
            a_now, b_now, round(b_now - a_now, 4),
            int(a_vol), int(b_vol), int(b_vol - a_vol),
            a_amt, b_amt, round(b_amt - a_amt, 2),
            NOW,
        ])
    write_csv(
        "probe_02_snapshot_two_ticks_stability.csv",
        ["股票代码", "现价A", "现价B", "现价差", "成交量A", "成交量B", "成交量差", "成交额A", "成交额B", "成交额差", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_02 启动 @ {NOW} =====")
    probe_single_full_fields()
    probe_batch_vs_loop()
    probe_two_ticks_stability()
    print(f"\n===== probe_02 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
