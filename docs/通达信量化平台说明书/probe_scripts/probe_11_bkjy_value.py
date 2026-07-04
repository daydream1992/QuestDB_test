# -*- coding: utf-8 -*-
# @meta 接口名称=get_bkjy_value / get_bkjy_value_by_date（板块交易信息）
# @meta 所属文档=a行情类信息/获取板块交易信息.md, a行情类信息/根据时间段获取板块交易信息.md
# @meta 探测目标=1) 各板块类型历史窗口；2) 同一板块同一天单日期 vs 区间一致性
"""
运行: python probe_11_bkjy_value.py
"""

import sys
import os
import csv
import time
from datetime import datetime, timedelta

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


def _get_sectors():
    try:
        lst = tq.get_sector_list()
    except Exception:
        return [], {}
    if not isinstance(lst, list):
        return [], {}
    by_type = {}
    for s in lst:
        if isinstance(s, dict):
            bt = str(s.get("BlockType", "") or str(s.get("Type", ""))
            code = s.get("Code", "") or s.get("SectorCode", "")
            name = s.get("BlockName", "") or s.get("Name", "")
            by_type.setdefault(bt, []).append((code, name))
    return lst, by_type


def probe_sector_matrix():
    print("\n>>> [探测1] 各板块类型历史窗口矩阵")
    _, by_type = _get_sectors()
    print(f"    板块类型数量: { {k: len(v) for k, v in by_type.items()}")
    WINDOWS = [30, 60, 90, 120, 180, 250, 365]
    today = datetime.now()
    rows = []
    for btype, pairs in by_type.items():
        for code, name in pairs[:3]:
            for win in WINDOWS:
                start = (today - timedelta(days=win)).strftime("%Y-%m-%d")
                end = today.strftime("%Y-%m-%d")
                t0 = time.time()
                try:
                    res = tq.get_bkjy_value_by_date(sector_code=code, start_date=start, end_date=end)
                    n = len(res) if isinstance(res, list) else -1
                    if isinstance(res, list) and res and isinstance(res[0], dict):
                        keys = ",".join(list(res[0].keys())[:15])
                        first_c = str(res[0].get("Close", ""))
                    else:
                        keys = ""
                        first_c = ""
                    err = ""
                except Exception as e:
                    n = -1
                    keys = ""
                    first_c = ""
                    err = str(e)
                cost_ms = int((time.time() - t0) * 1000)
                rows.append([btype, code, name, win, start, end, n, first_c, keys, cost_ms, err, NOW])

    write_csv(
        "probe_11_bk_sector_matrix.csv",
        ["板块类型", "板块代码", "板块名称", "窗口天", "start", "end", "返回条数", "首Close", "字段(前15)", "耗时ms", "错误", "探测时间"],
        rows,
    )


def probe_single_vs_range():
    print("\n>>> [探测2] 单日期 vs 区间同一天")
    _, by_type = _get_sectors()
    today = datetime.now()
    test_dates = [
        (today - timedelta(days=5)).strftime("%Y-%m-%d"),
        (today - timedelta(days=12)).strftime("%Y-%m-%d"),
        (today - timedelta(days=20)).strftime("%Y-%m-%d"),
    ]
    rows = []
    for btype, pairs in by_type.items():
        for code, name in pairs[:2]:
            for d in test_dates:
                try:
                    r1 = tq.get_bkjy_value(sector_code=code, date_list=[d], count=1)
                except Exception:
                    r1 = None
                try:
                    r2 = tq.get_bkjy_value_by_date(sector_code=code, start_date=d, end_date=d)
                except Exception:
                    r2 = None

                def _parse(r):
                    if not r:
                        return "", ""
                    if isinstance(r, list) and r and isinstance(r[0], dict):
                        it = r[0]
                        return str(it.get("Date", "")), f"Open={it.get('Open','')};Close={it.get('Close','')};Volume={it.get('Volume','')}"
                    return "", str(r)[:150]
                d1, info1 = _parse(r1)
                d2, info2 = _parse(r2)

                def _norm(x): return x.replace("-", "")
                match = "OK" if (d1 and d2 and _norm(d1) == _norm(d2)) else ("缺失单日期" if not d1 else "缺失区间" if not d2 else "不一致")
                rows.append([btype, code, name, d, d1, info1, d2, info2, match, NOW])

    write_csv(
        "probe_11_bk_single_vs_range.csv",
        ["板块类型", "板块代码", "板块名称", "测试日期", "单日期-Date", "单日期-Info", "区间-Date", "区间-Info", "一致性", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_11 启动 @ {NOW} =====")
    probe_sector_matrix()
    probe_single_vs_range()
    print(f"===== probe_11 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S') =====")
