# -*- coding: utf-8 -*-
# @meta 接口名称=formula_process_mul_zb / _xg / _exp（批量调用公式,无需 set_data）
# @meta 所属文档=f 调用通达信公式/批量调用通达信公式.md
# @meta 探测目标=1) 批量 zb(MACD)/xg(UPN) 多股并行; 2) 批量 vs 单股(probe_17)结果一致性; 3) count/return_count 参数行为
"""
运行: python probe_18_formula_batch_mul.py
输出: csv_outputs/probe_18_formula_batch_mul.csv
说明: 批量调用无需 formula_set_data_info, 一次传 stock_list; 选股用 _xg, 指标用 _zb。
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


TEST_STOCKS = ["600519.SH", "688318.SH", "300750.SZ", "000858.SZ", "510300.SH"]


def last_val(code_block, key):
    """批量返回 res[code] = {key: [{'Date','Value'},...]} 取最近非None的Value(float)"""
    if not isinstance(code_block, dict):
        return None
    lst = code_block.get(key, [])
    if not isinstance(lst, list):
        return None
    for it in reversed(lst):
        if isinstance(it, dict):
            v = it.get('Value')
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return v
        elif it is not None:
            return it
    return None


def probe_batch():
    rows = []
    print("\n>>> [探测1] 批量指标 formula_process_mul_zb(MACD)")
    t0 = time.time()
    err_zb = ""
    try:
        res_zb = tq.formula_process_mul_zb(
            formula_name='MACD', formula_arg='12,26,9', xsflag=4,
            return_count=1, return_date=True,
            stock_list=TEST_STOCKS, stock_period='1d', count=30, dividend_type=1)
    except Exception as e:  # noqa: BLE001
        res_zb = {}
        err_zb = str(e)[:80]
    cost_zb = int((time.time() - t0) * 1000)
    eid_zb = res_zb.get('ErrorId', '?') if isinstance(res_zb, dict) else '?'
    print(f"    批量MACD: ErrorId={eid_zb} ({cost_zb}ms) {err_zb}")

    print("\n>>> [探测2] 批量选股 formula_process_mul_xg(UPN)")
    t0 = time.time()
    err_xg = ""
    try:
        res_xg = tq.formula_process_mul_xg(
            formula_name='UPN', formula_arg='3',
            return_count=1, return_date=True,
            stock_list=TEST_STOCKS, stock_period='1d', count=30, dividend_type=1)
    except Exception as e:  # noqa: BLE001
        res_xg = {}
        err_xg = str(e)[:80]
    cost_xg = int((time.time() - t0) * 1000)
    eid_xg = res_xg.get('ErrorId', '?') if isinstance(res_xg, dict) else '?'
    print(f"    批量UPN: ErrorId={eid_xg} ({cost_xg}ms) {err_xg}")

    # 汇总每只票的最后一个值
    for code in TEST_STOCKS:
        zb_dif = last_val(res_zb.get(code, {}), 'DIF') if isinstance(res_zb, dict) else None
        xg_up3 = last_val(res_xg.get(code, {}), 'UP3') if isinstance(res_xg, dict) else None
        rows.append([code, code.split(".")[-1], zb_dif, xg_up3, eid_zb, eid_xg, cost_zb, cost_xg, NOW])
        print(f"    {code}: MACD_DIF={zb_dif} UPN={xg_up3}")

    write_csv("probe_18_formula_batch_mul.csv",
              ["代码", "市场", "批量MACD_DIF", "批量UPN(UP3)",
               "zb_ErrorId", "xg_ErrorId", "zb耗时ms", "xg耗时ms", "探测时间"], rows)
    print(f"\n    [对比] 批量一次 {cost_zb}ms 拿全部, vs probe_17 单股逐只累计; 批量无需 set_data_info")


if __name__ == "__main__":
    print(f"===== probe_18 启动 @ {NOW} =====")
    probe_batch()
    tq.close()
    print(f"===== probe_18 完成 =====")
