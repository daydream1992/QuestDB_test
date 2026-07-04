# -*- coding: utf-8 -*-
# @meta 接口名称=get_gpjy_value / get_gpjy_value_by_date（股票交易信息）
# @meta 所属文档=a行情类信息/获取股票的交易信息.md, a行情类信息/根据时间段获取股票交易信息.md
# @meta 探测目标=1) 单日期 vs 区间返回同一天数据一致性；2) 30/90/180/365 历史回溯；3) 多市场股票覆盖情况（主板/创业板/科创板）
"""
运行: python probe_10_gpjy_value.py
输出:
    probe_10_gpjy_single_vs_range.csv —— 单日期 vs 区间
    probe_10_gpjy_window_matrix.csv —— 不同窗口历史返回条数矩阵
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


TEST_STOCKS = [
    "600519.SH",  # 沪主板
    "000858.SZ",  # 深主板
    "300750.SZ",  # 创业板
    "688318.SH",  # 科创板
]


def probe_single_vs_range():
    print("\n>>> [探测1] 单日期 vs 区间一致性")
    today = datetime.now()
    test_dates = [
        (today - timedelta(days=3)).strftime("%Y-%m-%d"),
        (today - timedelta(days=7)).strftime("%Y-%m-%d"),
        (today - timedelta(days=14)).strftime("%Y-%m-%d"),
        (today - timedelta(days=20)).strftime("%Y-%m-%d"),
    ]

    rows = []
    for code in TEST_STOCKS:
        for d in test_dates:
            try:
                r1 = tq.get_gpjy_value(stock_code=code, date_list=[d], count=1)
            except Exception as e:
                r1 = None
            try:
                r2 = tq.get_gpjy_value_by_date(stock_code=code, start_date=d, end_date=d)
            except Exception as e:
                r2 = None

            def _parse(r):
                if not r:
                    return "", ""
                if isinstance(r, list) and r and isinstance(r[0], dict):
                    it = r[0]
                    return str(it.get("Date", "")), f"Open={it.get('Open','')};Close={it.get('Close','')};High={it.get('High','')};Low={it.get('Low','')};Volume={it.get('Volume','')};Amount={it.get('Amount','')}"
                return "", str(r)[:200]

            d1, info1 = _parse(r1)
            d2, info2 = _parse(r2)

            def _norm(x): return x.replace("-", "")
            match = "OK" if (d1 and d2 and _norm(d1) == _norm(d2)) else ("缺失单日期" if not d1 else "缺失区间" if not d2 else "不一致")
            rows.append([code, d, d1, info1, d2, info2, match, NOW])

    write_csv(
        "probe_10_gpjy_single_vs_range.csv",
        ["股票代码", "测试日期", "单日期-Date", "单日期-Info",
         "区间-Date", "区间-Info", "一致性", "探测时间"],
        rows,
    )


def probe_window_matrix():
    print("\n>>> [探测2] 30/60/90/120/180/250/365 天历史窗口")
    today = datetime.now()
    WINDOWS = [30, 60, 90, 120, 180, 250, 365]
    rows = []
    for code in TEST_STOCKS:
        for win in WINDOWS:
            start = (today - timedelta(days=win)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            t0 = time.time()
            try:
                res = tq.get_gpjy_value_by_date(stock_code=code, start_date=start, end_date=end)
                n = len(res) if isinstance(res, list) else -1
                if isinstance(res, list) and res and isinstance(res[0], dict):
                    keys = ",".join(list(res[0].keys())[:15])
                    first_close = str(res[0].get("Close", ""))
                    last_close = str(res[-1].get("Close", ""))
                else:
                    keys = ""
                    first_close = ""
                    last_close = ""
                err = ""
            except Exception as e:
                n = -1
                keys = ""
                first_close = ""
                last_close = ""
                err = str(e)
            cost_ms = int((time.time() - t0) * 1000)
            rows.append([code, win, start, end, n, first_close, last_close, keys, cost_ms, err, NOW])

    write_csv(
        "probe_10_gpjy_window_matrix.csv",
        ["股票代码", "窗口天", "start", "end", "返回条数", "首收价", "末收价", "字段(前15)", "耗时ms", "错误", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_10 启动 @ {NOW} =====")
    probe_single_vs_range()
    probe_window_matrix()
    print(f"===== probe_10 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
