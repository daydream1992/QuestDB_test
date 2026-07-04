# -*- coding: utf-8 -*-
# @meta 接口名称=get_kzz_info（可转债信息）
# @meta 所属文档=a行情类信息/获取可转债基本信息.md
# @meta 探测目标=1) 可转债基本信息字段覆盖率；2) 与正股对应关系
"""
运行: python probe_13_kzz_info.py
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


KZZ_CODES = [
    "113050.SH", "127045.SZ", "113016.SH", "128136.SZ",
    "110059.SH", "128141.SZ", "113548.SH", "127030.SZ",
    "113585.SH", "110076.SH", "128095.SZ", "110084.SH",
    "128140.SZ", "113578.SH", "128119.SZ", "113044.SH",
    "113595.SH", "128131.SZ", "127012.SZ", "110030.SH",
]


def probe_kzz():
    print("\n>>> [探测1] 可转债基本信息字段覆盖")
    rows = []
    all_keys = set()
    for code in KZZ_CODES:
        t0 = time.time()
        try:
            r = tq.get_kzz_info(stock_code=code, field_list=[])
        except Exception as e:
            r = None
            err = str(e)
        if isinstance(r, dict):
            keys = list(r.keys())
            all_keys.update(keys)
            rows.append([
                code, len(keys),
                r.get("BondName", ""),
                r.get("ConvCode", ""),
                str(r.get("ParValue", "")),
                str(r.get("ConvPrice", "")),
                str(r.get("ConvRatio", "")),
                str(r.get("Rate", "")),
                str(r.get("StartDate", "")),
                str(r.get("EndDate", "")),
                str(r.get("InterestRate", "")),
                int((time.time() - t0) * 1000),
                NOW,
            ])
        else:
            rows.append([code, 0, "", "", "", "", "", "", "", "", "", int((time.time() - t0) * 1000), NOW])

    write_csv(
        "probe_13_kzz_info.csv",
        ["可转债代码", "返回字段数", "债券名称", "转换代码", "面值",
         "转股价格", "转股比例", "利率", "转股开始日期",
         "转股结束日期", "利率详情", "耗时ms", "探测时间"],
        rows,
    )
    print(f"    字段并集: {sorted(all_keys)}")


if __name__ == "__main__":
    print(f"===== probe_13 启动 @ {NOW} =====")
    probe_kzz()
    print(f"===== probe_13 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S') =====")
