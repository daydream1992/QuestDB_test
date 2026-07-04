# -*- coding: utf-8 -*-
# @meta 接口名称=get_trackzs_etf_info（ETF跟踪指数）
# @meta 所属文档=a行情类信息/获取ETF跟踪指数.md
# @meta 探测目标=1) ETF 代码 vs 指数代码映射关系；2) 跟踪指数的 get_stock_info 一致性
"""
运行: python probe_14_etf_trackzs.py
输出:
    probe_14_etf_trackzs.csv
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
    with open(path, "w", encoding="utf-8-sig", "探测时间"],
        rows,
    )


# 常见 ETF 代码
ETF_CODES = [
    "510300.SH", "510050.SH", "510500.SH", "159915.SZ",
    "159901.SZ", "510330.SH", "159949.SZ", "510030.SH",
    "512170.SH", "512880.SH", "512690.SH", "510180.SH",
    "510210.SH", "159922.SZ", "159919.SZ", "512080.SH",
    "510030.SH", "159910.SZ", "510170.SH", "510510.SH",
]


def probe_etf():
    print("\n>>> [探测1] ETF 跟踪指数")
    rows = []
    for code in ETF_CODES:
        t0 = time.time()
        try:
            r = tq.get_trackzs_etf_info(etf_code=code)
        except Exception as e:
            r = None
            err = str(e)
        if isinstance(r, dict):
            keys = list(r.keys())
            rows.append([
                code, len(keys),
                r.get("Name", ""),
                r.get("IndexCode", ""),
                r.get("IndexName", ""),
                str(r.get("IndexRatio", "")),
                str(r.get("TrackError", "")),
                int((time.time() - t0) * 1000),
                NOW,
            ])
        else:
            rows.append([code, 0, "", "", "", "", "", int((time.time() - t0) * 1000), NOW])

    write_csv(
        "probe_14_etf_trackzs.csv",
        ["ETF代码", "返回字段数", "ETF名称",
         "指数代码", "指数名称",
         "跟踪比例", "跟踪误差", "耗时ms", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_14 启动 @ {NOW} =====")
    probe_etf()
    print(f"===== probe_14 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S') =====")
