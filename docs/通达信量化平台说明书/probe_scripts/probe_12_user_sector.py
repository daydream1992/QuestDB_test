# -*- coding: utf-8 -*-
# @meta 接口名称=get_user_sector + get_user_sector_by_code（自定义板块）
# @meta 所属文档=a行情类信息/获取自定义板块信息.md, a行情类信息/根据板块代码获取自定义板块信息.md
# @meta 探测目标=1) 用户自定义板块列表 + 成份股对比；2) 用户自定义板块 vs get_stock_list_in_sector 一致；3) 是否支持自定义代码的组成成份列表
"""
运行: python probe_12_user_sector.py
输出:
    probe_12_user_sector_cross.csv
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


def probe_user_sector():
    print("\n>>> [探测1] 自定义板块列表")
    try:
        sectors = tq.get_user_sector()
    except Exception as e:
        sectors = None
        print(f"    get_user_sector 异常: {e}")
        return

    rows = []
    if not isinstance(sectors, list):
        print(f"    get_user_sector 非列表: {type(sectors)}")
        return

    print(f"    自定义板块数: {len(sectors)}")
    for s in sectors:
        if not isinstance(s, dict):
            continue
        code = s.get("BlockCode", "") or s.get("SectorCode", "") or s.get("Code", "")
        name = s.get("BlockName", "") or s.get("Name", "")
        # 自定义板块成份股
        t0 = time.time()
        try:
            r = tq.get_user_sector_by_code(sector_code=code)
            n1 = len(r) if isinstance(r, list) else 0
            names1 = [x.get("Code", "") for x in (r if isinstance(r, list) else []]
        except Exception as e:
            n1 = 0
            names1 = []
        cost1 = int((time.time() - t0) * 1000)
        # 对照：用通用 get_stock_list_in_sector
        try:
            r2 = tq.get_stock_list_in_sector(sector_code=code)
            n2 = len(r2) if isinstance(r2, list) else 0
        except Exception:
            n2 = 0

        set1 = set([x for x in names1 if x)
        rows.append([
            code, name,
            n1, n2,
        len(set1), " | ".join(list(set1)[:10]),
            cost1, NOW,
        ])

    write_csv(
        "probe_12_user_sector_cross.csv",
        ["板块代码", "板块名称", "自定义成份数", "通用成份数", "去重代码数", "前10代码", "耗时ms", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_12 启动 @ {NOW} =====")
    probe_user_sector()
    print(f"===== probe_12 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S') =====")
