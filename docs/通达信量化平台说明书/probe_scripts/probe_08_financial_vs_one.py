# -*- coding: utf-8 -*-
# @meta 接口名称=get_financial_data / get_gp_one_data（专业财务 vs 个股单条财务）
# @meta 所属文档=b财务类数据/获取专业财务数据.md, b财务类数据/获取股票的单个财务数据.md
# @meta 探测目标=1) 同一股票在同一报告期，两个接口返回的财务字段一致性；2) 多股票批量 vs 单条遍历效率；3) report_type = announce_time/tag_time 差异
"""
运行: python probe_08_financial_vs_one.py
输出:
    probe_08_fn_cross_reporttype.csv —— announce_time vs tag_time 对比
    probe_08_fn_vs_go_consistency.csv —— 财务字段 vs 个股单条指标的一致性
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


TEST_STOCKS = ["600519.SH", "000858.SZ", "300750.SZ", "600036.SH", "688318.SH"]


# ============================================================
# 探测 1) report_type: announce_time vs tag_time
# ============================================================
def probe_report_type():
    print("\n>>> [探测1] report_type announce_time vs tag_time")
    rows = []
    # 选择三个典型字段测试（营业收入/净利润/每股收益）
    test_fields = ["FN230", "FN232", "FN206"]  # 营业收入 / 归母净利润 / 扣非净利润

    for code in TEST_STOCKS:
        for rt in ["announce_time", "tag_time"]:
            t0 = time.time()
            try:
                res = tq.get_financial_data(
                    stock_list=[code],
                    field_list=test_fields,
                    start_time="2023-01-01",
                    end_time="2025-12-31",
                    report_type=rt,
                )
                # 返回是 dict，key = stock_code，value 是 list[dict] 或 DataFrame
                records = None
                if isinstance(res, dict) and code in res:
                    val = res[code]
                    if hasattr(val, "__iter__"):
                        try:
                            records = list(val)
                        except Exception:
                            records = None
                n = len(records) if records else 0
                sample = str(records[0])[:200] if records and len(records) else ""
                err = ""
            except Exception as e:
                n = -1
                sample = ""
                err = str(e)
            cost_ms = int((time.time() - t0) * 1000)
            rows.append([code, rt, n, sample, cost_ms, err, NOW])

    write_csv(
        "probe_08_fn_cross_reporttype.csv",
        ["股票代码", "report_type", "返回记录数", "首条样例", "耗时ms", "错误", "探测时间"],
        rows,
    )


# ============================================================
# 探测 2) get_financial_data（最新一条）vs get_gp_one_data 相关字段对比
# ============================================================
def probe_fn_vs_go():
    print("\n>>> [探测2] get_financial_data 最新值 vs get_gp_one_data 单条财务")
    # 选择一致预期/最新财报相关字段
    go_fields = ["GO1", "GO2", "GO5", "GO6", "GO11", "GO12", "GO17", "GO18"]  # 发行价/发行数量/EPS/营收/每股净资产
    rows = []
    for code in TEST_STOCKS:
        go_res = {}
        try:
            go = tq.get_gp_one_data(stock_list=[code], field_list=go_fields)
            if isinstance(go, dict):
                go_res = go.get(code, {}) or {}
        except Exception as e:
            print(f"    {code} get_gp_one_data 异常: {e}")

        # 再调用 get_financial_data 找最近一期的 EPS / 营业收入 / 每股净资产
        try:
            fn_res = tq.get_financial_data(
                stock_list=[code],
                field_list=["FN230", "FN232", "FN1"],  # 营业收入 / 净利润 / 基本每股收益
                start_time="2024-01-01",
                end_time="",
                report_type="announce_time",
            )
        except Exception as e:
            fn_res = {}

        latest_fn = {}
        if isinstance(fn_res, dict) and code in fn_res:
            try:
                records = list(fn_res[code]) if hasattr(fn_res[code], "__iter__") else []
                if records:
                    latest = records[-1]
                    if isinstance(latest, dict):
                        latest_fn = latest
                    else:
                        # DataFrame row
                        latest_fn = {k: latest[k] for k in ["FN230", "FN232", "FN1"] if k in latest}
            except Exception:
                latest_fn = {}

        rows.append([
            code,
            go_res.get("GO1", ""), go_res.get("GO2", ""),
            go_res.get("GO5", ""), go_res.get("GO6", ""),
            go_res.get("GO11", ""), go_res.get("GO12", ""),
            go_res.get("GO17", ""), go_res.get("GO18", ""),
            latest_fn.get("FN230", ""), latest_fn.get("FN232", ""), latest_fn.get("FN1", ""),
            NOW,
        ])

    write_csv(
        "probe_08_fn_vs_go_consistency.csv",
        ["股票代码",
         "GO1发行价", "GO2发行数量",
         "GO5预期EPS", "GO6预期EPS次年",
         "GO11预期营业收入", "GO12预期营业收入次年",
         "GO17预期每股净资产", "GO18预期每股净资产次年",
         "FN230营业收入最新", "FN232净利润最新", "FN1每股收益最新",
         "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_08 启动 @ {NOW} =====")
    probe_report_type()
    probe_fn_vs_go()
    print(f"===== probe_08 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
