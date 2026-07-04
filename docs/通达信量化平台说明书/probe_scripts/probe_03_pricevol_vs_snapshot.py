# -*- coding: utf-8 -*-
# @meta 接口名称=get_pricevol（批量价量）+ get_market_snapshot（单只快照）
# @meta 所属文档=a行情类信息/批量获取价量.md, a行情类信息/获取快照数据.md
# @meta 探测目标=1) 批量价量 LastClose/Now/Volume 与 单只快照一致性；2) 跨市场覆盖（沪/深/科/ETF/可转债/板块指数）
"""
运行: python probe_03_pricevol_vs_snapshot.py
输出:
    probe_03_pricevol_vs_snapshot.csv — 批量价量 vs 单只快照
    probe_03_pricevol_market_matrix.csv — 不同 market 参数覆盖测试
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


def probe_pricevol_vs_snapshot():
    print("\n>>> [探测1] 批量价量 vs 单只快照 一致性")
    codes = []
    labels = []
    for mk, lbl in [("5", "A股"), ("10", "板块指数"), ("31", "ETF"), ("32", "可转债")]:
        try:
            lst = tq.get_stock_list(market=mk, list_type=1) or []
            codes.extend([s.get("Code", "") for s in lst[:40]])
            labels.extend([lbl] * min(40, len(lst)))
        except Exception as e:
            print(f"    market={mk} 失败: {e}")

    seen = set()
    uc, ul = [], []
    for c, lbl in zip(codes, labels):
        if c and c not in seen:
            seen.add(c)
            uc.append(c)
            ul.append(lbl)
    uc = uc[:150]
    ul = ul[:150]
    print(f"    对比股票数: {len(uc)}")

    # 批量价量
    t0 = time.time()
    pv = {}
    try:
        pv_res = tq.get_pricevol(stock_list=uc)
        if isinstance(pv_res, dict):
            pv = pv_res
    except Exception as e:
        print(f"    get_pricevol 异常: {e}")
    batch_ms = int((time.time() - t0) * 1000)

    # 逐只快照
    t0 = time.time()
    snap = {}
    for c in uc:
        try:
            r = tq.get_market_snapshot(stock_code=c, field_list=["Now", "LastClose", "Volume"])
            snap[c] = r or {}
        except Exception:
            snap[c] = {}
    loop_ms = int((time.time() - t0) * 1000)

    rows = []
    for c, lbl in zip(uc, ul):
        pr = pv.get(c) or {}
        sr = snap.get(c) or {}
        pn = pr.get("Now", ""); sn = sr.get("Now", "")
        pv_vol = pr.get("Volume", ""); sv_vol = sr.get("Volume", "")
        plc = pr.get("LastClose", ""); slc = sr.get("LastClose", "")
        try:
            now_diff = round(float(pn) - float(sn), 4)
        except Exception:
            now_diff = ""
        try:
            vol_diff = int(float(pv_vol)) - int(float(sv_vol)) if (pv_vol and sv_vol) else ""
        except Exception:
            vol_diff = ""
        rows.append([lbl, c, pn, sn, now_diff, pv_vol, sv_vol, vol_diff, plc, slc, batch_ms, loop_ms, NOW])

    write_csv(
        "probe_03_pricevol_vs_snapshot.csv",
        ["标签", "代码", "批量-Now", "快照-Now", "Now差",
         "批量-Volume", "快照-Volume", "Volume差",
         "批量-LastClose", "快照-LastClose", "批量耗时ms", "逐只耗时ms", "探测时间"],
        rows,
    )
    diff_count = sum(1 for r in rows if r[4] not in ("", 0, 0.0))
    print(f"    Now 不一致: {diff_count}/{len(rows)}")


def probe_market_matrix():
    print("\n>>> [探测2] 不同 market 覆盖矩阵")
    markets = [
        ("5", "全部A股"), ("7", "上证主板"), ("8", "深证主板"),
        ("31", "ETF基金"), ("32", "可转债"), ("33", "LOF基金"),
        ("50", "沪深A股"), ("51", "创业板"), ("52", "科创板"), ("53", "北交所"),
    ]
    rows = []
    for mk, lbl in markets:
        t0 = time.time()
        count = 0
        err = ""
        codes = []
        try:
            lst = tq.get_stock_list(market=mk, list_type=1) or []
            count = len(lst)
            codes = [s.get("Code", "") for s in lst[:30]]
        except Exception as e:
            err = str(e)
        cost_ms = int((time.time() - t0) * 1000)

        pv_count = -1
        pv_err = ""
        try:
            if codes:
                res = tq.get_pricevol(stock_list=codes)
                if isinstance(res, dict):
                    pv_count = len(res)
        except Exception as e:
            pv_err = str(e)

        rows.append([mk, lbl, count, "|".join(codes[:5]), cost_ms, err, pv_count, pv_err, NOW])

    write_csv(
        "probe_03_pricevol_market_matrix.csv",
        ["market值", "market名称", "market总数", "前5代码样",
         "get_stock_list耗时ms", "get_stock_list错误",
         "批量价量返回条数", "批量价量错误", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_03 启动 @ {NOW} =====")
    probe_pricevol_vs_snapshot()
    probe_market_matrix()
    print(f"===== probe_03 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
