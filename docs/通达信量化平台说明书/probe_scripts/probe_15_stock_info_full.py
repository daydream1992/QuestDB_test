# -*- coding: utf-8 -*-
# @meta 接口名称=get_stock_info（证券基本信息完整字段探测）
# @meta 所属文档=a行情类信息/获取证券基本信息.md
# @meta 探测目标=1) 全量字段探测（不同市场/不同板块股票）；2) field_list 空列表 vs 特定字段；3) 跨市场字段差异
"""
运行: python probe_15_stock_info_full.py
输出:
    probe_15_stock_info_full.csv
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


TEST_STOCKS = [
    "600519.SH",  # 沪主板
    "688318.SH",  # 科创板
    "300750.SZ",  # 创业板
    "000858.SZ",  # 深主板
    "510300.SH",  # ETF
    "113050.SH",  # 可转债
    "127045.SZ",  # 可转债
    "000300.SH",  # 沪深300指数
    "159915.SZ",  # 创业板ETF
]


def probe_stock_info_full():
    print("\n>>> [探测1] 全量字段探测")
    rows = []
    field_union = set()
    field_market_sets = {}  # 按市场聚合
    for code in TEST_STOCKS:
        t0 = time.time()
        try:
            # field_list=[]
            r = tq.get_stock_info(stock_code=code, field_list=[])
        except Exception as e:
            r = None
            err = str(e)
        cost = int((time.time() - t0) * 1000)
        if isinstance(r, dict):
            keys = list(r.keys())
            field_union.update(keys)
            market_prefix = code.split(".")[-1] if "." in code else "NA"
            for k in keys:
                field_market_sets.setdefault(market_prefix, set()).add(k)
            rows.append([
                code, market_prefix,
                len(keys),
                r.get("Name", ""),
                r.get("Code", ""),
                r.get("HSStockKind", ""),
                str(r.get("ActiveCapital", "")),
                str(r.get("TotalCapital", "")),
                str(r.get("IssueDate", "")),
                r.get("HYName", ""),
                str(r.get("GPNume", "")),
                cost, NOW,
            ])
        else:
            rows.append([code, "NA", 0, "", "", "", "", "", "", "", "", cost, NOW])

    write_csv(
        "probe_15_stock_info_full.csv",
        ["股票代码", "市场", "字段数", "名称",
         "代码", "股票类型", "流通股本", "总股本",
         "上市日期", "所属行业", "GPNume", "耗时ms", "探测时间"],
        rows,
    )
    print(f"    字段并集({len(field_union)}): {sorted(field_union)}")
    for m, s in field_market_sets.items():
        print(f"    市场[{m}] 字段数: {len(s)}")


if __name__ == "__main__":
    print(f"===== probe_15 启动 @ {NOW} =====")
    probe_stock_info_full()
    print(f"===== probe_15 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S') =====")
