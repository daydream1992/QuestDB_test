# -*- coding: utf-8 -*-
# @meta 接口名称=get_gb_info（指定日期股本） / get_gb_info_by_date（时间段股本）
# @meta 所属文档=a行情类信息/获取每天的股本数据.md, a行情类信息/根据时间段获取股本数据.md
# @meta 探测目标=1) 单指定日期 vs 日期区间返回的同一天股本是否一致；2) 不同股票的 30/90/180/365 天窗口的历史回溯能力；
# @meta 探测目标_续=3) 非交易日的返回空值情况
"""
运行: python probe_04_gb_info_history.py
输出:
    probe_04_gb_date_window.csv —— 不同历史窗口下的返回条数
    probe_04_gb_single_vs_range.csv —— 指定日期 vs 区间返回的同一天对比
    probe_04_gb_non_trade_day.csv —— 非交易日测试
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
    "688318.SH", "600519.SH", "000001.SZ", "300750.SZ",
    "600036.SH", "000858.SZ", "601318.SH", "600276.SH",
]

# 测试历史窗口（天）
WINDOWS = [30, 60, 90, 120, 180, 250, 365]


# ============================================================
# 探测 1) 不同历史窗口 get_gb_info_by_date 可返回的条目数
# ============================================================
def probe_history_windows():
    print("\n>>> [探测1] 不同历史窗口测试")
    today = datetime.now()

    rows = []
    for code in TEST_STOCKS:
        for days in WINDOWS:
            start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            t0 = time.time()
            try:
                res = tq.get_gb_info_by_date(stock_code=code, start_date=start, end_date=end)
                if isinstance(res, list):
                    n = len(res)
                else:
                    n = -1
                err = ""
                sample = str(res[0]) if isinstance(res, list) and res else ""
            except Exception as e:
                n = -1
                err = str(e)
                sample = ""
            cost_ms = int((time.time() - t0) * 1000)
            rows.append([code, days, start, end, n, cost_ms, err, sample[:200], NOW])

    write_csv(
        "probe_04_gb_date_window.csv",
        ["股票代码", "窗口天数", "起始日", "结束日", "返回条数", "耗时ms", "错误", "样例", "探测时间"],
        rows,
    )


# ============================================================
# 探测 2) 单指定日期 vs 区间返回的同一天一致性
# ============================================================
def probe_single_vs_range():
    print("\n>>> [探测2] 单指定日期 vs 区间返回同一天 一致性")
    today = datetime.now()
    test_dates = [
        (today - timedelta(days=7)).strftime("%Y-%m-%d"),
        (today - timedelta(days=15)).strftime("%Y-%m-%d"),
        (today - timedelta(days=30)).strftime("%Y-%m-%d"),
    ]

    rows = []
    for code in TEST_STOCKS[:5]:
        for d in test_dates:
            # 单日期
            try:
                res_single = tq.get_gb_info(
                    stock_code=code,
                    date_list=[d],
                    count=1,
                )
            except Exception as e:
                res_single = None
                err_single = str(e)
            # 区间取同一天
            try:
                res_range = tq.get_gb_info_by_date(stock_code=code, start_date=d, end_date=d)
            except Exception as e:
                res_range = None
                err_range = str(e)

            # 解析两个结果
            def _parse(r):
                if not r:
                    return "", ""
                if isinstance(r, list) and r:
                    first = r[0]
                    return str(first.get("Date", "")), f"Zgb={first.get('Zgb','')}/Ltgb={first.get('Ltgb','')}"
                if isinstance(r, dict):
                    return str(r.get("Date", "")), f"Zgb={r.get('Zgb','')}/Ltgb={r.get('Ltgb','')}"
                return "", str(r)[:50]

            d1, info1 = _parse(res_single)
            d2, info2 = _parse(res_range)
            # 日期格式 yyyy-mm-dd vs yyyymmdd 转换
            def _norm(x):
                return x.replace("-", "")
            match = "OK" if (_norm(d1) and _norm(d2) and _norm(d1) == _norm(d2)) else ("无单日期" if not d1 else "无区间" if not d2 else "日期/字段不一致")
            rows.append([code, d, d1, info1, d2, info2, match, NOW])

    write_csv(
        "probe_04_gb_single_vs_range.csv",
        ["股票代码", "测试日期", "单日期-Date", "单日期-Info",
         "区间-Date", "区间-Info", "一致性", "探测时间"],
        rows,
    )


# ============================================================
# 探测 3) 非交易日测试（周末/节假日）
# ============================================================
def probe_non_trade_day():
    print("\n>>> [探测3] 非交易日返回")
    # 往前找最近的几个周末
    today = datetime.now()
    weekend_dates = []
    for back in range(1, 20):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # 5=周六 6=周日
            weekend_dates.append(d.strftime("%Y-%m-%d"))
        if len(weekend_dates) >= 4:
            break

    rows = []
    for code in TEST_STOCKS[:3]:
        for d in weekend_dates:
            try:
                res = tq.get_gb_info_by_date(stock_code=code, start_date=d, end_date=d)
                n = len(res) if isinstance(res, list) else -1
                info = str(res[0]) if isinstance(res, list) and res else ""
            except Exception as e:
                n = -1
                info = f"err:{e}"
            rows.append([code, d, n, info[:200], NOW])

    write_csv(
        "probe_04_gb_non_trade_day.csv",
        ["股票代码", "周末日期", "返回条数", "返回内容", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_04 启动 @ {NOW} =====")
    probe_history_windows()
    probe_single_vs_range()
    probe_non_trade_day()
    print(f"===== probe_04 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
