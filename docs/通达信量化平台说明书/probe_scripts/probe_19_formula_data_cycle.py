# -*- coding: utf-8 -*-
# @meta 接口名称=formula_set_data / formula_get_data / formula_format_data（公式数据 设置-读取-格式化 闭环）
# @meta 所属文档=f 调用通达信公式/向通达信公式设置数据.md + 获取公式中的设置数据.md + 格式化K线数据.md
# @meta 探测目标=1) get_market_data→format_data→set_data→get_data 完整闭环; 2) set_data 读回数据一致性(注意 Amount 单位:format万元/get元); 3) Close 字段对齐验证
"""
运行: python probe_19_formula_data_cycle.py
输出: csv_outputs/probe_19_formula_data_cycle.csv
说明: formula_set_data 需先用 formula_format_data 把 get_market_data 的数据格式化;
      ⚠️ format_data 的 Amount 是万元, get_data 读回的是元(单位不同), 只对比 Close。
"""
import sys
import os
import csv
import time
from datetime import datetime

sys.path.append(r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq  # noqa: E402

tq.initialize(__file__)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_outputs")
os.makedirs(OUT_DIR, exist_ok=True)
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_csv(filename, headers, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([headers] + rows)
    print(f"[OK] {path} ({len(rows)} 行)")


CODE = "688318.SH"


def probe_cycle():
    rows = []
    print(f"\n>>> 公式数据闭环 (以 {CODE} 为例)")

    # 1. get_market_data 取原始K线
    t0 = time.time()
    try:
        md = tq.get_market_data(stock_list=[CODE], count=5, period='1d', dividend_type=1)
    except Exception as e:  # noqa: BLE001
        md = {}
        print(f"    [1] FAIL get_market_data: {e}")
    cost1 = int((time.time() - t0) * 1000)
    n_md = len(md['Close']) if isinstance(md, dict) and 'Close' in md else 0
    print(f"    [1] get_market_data: {n_md} 条 ({cost1}ms)")

    # 2. formula_format_data 格式化为公式可识别格式
    t0 = time.time()
    try:
        fmt = tq.formula_format_data(md)
    except Exception as e:  # noqa: BLE001
        fmt = {}
        print(f"    [2] FAIL format_data: {e}")
    cost2 = int((time.time() - t0) * 1000)
    fmt_list = fmt.get(CODE, []) if isinstance(fmt, dict) else []
    n_fmt = len(fmt_list)
    fmt_keys = list(fmt_list[0].keys()) if fmt_list else []
    print(f"    [2] format_data: {n_fmt} 条, 字段={fmt_keys} ({cost2}ms)")

    # 3. formula_set_data 设置(用 format 后的数据)
    t0 = time.time()
    try:
        set_res = tq.formula_set_data(
            stock_code=CODE, stock_period='1d',
            stock_data=fmt_list, count=len(fmt_list), dividend_type=1)
    except Exception as e:  # noqa: BLE001
        set_res = {}
        print(f"    [3] FAIL set_data: {e}")
    cost3 = int((time.time() - t0) * 1000)
    set_ok = set_res.get('ErrorId', '?') if isinstance(set_res, dict) else '?'
    print(f"    [3] set_data: ErrorId={set_ok} ({cost3}ms)")

    # 4. formula_get_data 读回
    t0 = time.time()
    try:
        gd = tq.formula_get_data()
    except Exception as e:  # noqa: BLE001
        gd = {}
        print(f"    [4] FAIL get_data: {e}")
    cost4 = int((time.time() - t0) * 1000)
    gd_code = gd.get('Code', '') if isinstance(gd, dict) else ''
    gd_data = gd.get('Value', []) if isinstance(gd, dict) else []
    n_gd = len(gd_data)
    gd_keys = list(gd_data[0].keys()) if gd_data else []

    # 闭环一致性: 只比 Close(format 和 get_data 的 Close 单位一致, 都是元)
    read_close = gd_data[-1].get('Close') if gd_data else None
    set_close = fmt_list[-1].get('Close') if fmt_list else None
    if read_close is not None and set_close is not None:
        consistent = "一致" if abs(float(read_close) - float(set_close)) < 1e-4 \
            else f"不一致(read={read_close} vs set={set_close})"
    else:
        consistent = f"缺失(read={read_close} vs set={set_close})"
    print(f"    [4] get_data: Code={gd_code} {n_gd}条 字段={gd_keys} ({cost4}ms)")
    print(f"    [闭环 Close] {consistent}")

    rows.append([CODE, n_md, n_fmt, "|".join(fmt_keys), set_ok,
                 gd_code, n_gd, "|".join(gd_keys), read_close, set_close, consistent,
                 cost1, cost2, cost3, cost4, NOW])
    write_csv("probe_19_formula_data_cycle.csv",
              ["代码", "market_data条数", "format条数", "format字段", "set_ErrorId",
               "get_Code", "get条数", "get字段", "读回Close", "设置Close", "Close一致性",
               "get_md耗时", "format耗时", "set耗时", "get耗时", "探测时间"], rows)


if __name__ == "__main__":
    print(f"===== probe_19 启动 @ {NOW} =====")
    probe_cycle()
    tq.close()
    print(f"===== probe_19 完成 =====")
