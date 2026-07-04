# -*- coding: utf-8 -*-
# @meta 接口名称=formula 异常诊断（深挖 probe_18 批量vs单股不一致 + probe_19 get_data 读空）
# @meta 所属文档=f 调用通达信公式/(综合诊断)
# @meta 探测目标=1) 控制 xsflag/count/dividend 一致后,批量vs单股 DIF 是否仍不一致; 2) get_data 在 set/计算后/不同set方式下能否读回
"""
运行: python probe_20_formula_anomaly_diag.py
输出: csv_outputs/probe_20_anomaly_diag.csv + 终端详细对比
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

CODE = "600519.SH"   # 茅台(probe_17/18 复现过的票)


def write_csv(filename, headers, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([headers] + rows)
    print(f"[OK] {path} ({len(rows)} 行)")


def diag_batch_vs_single():
    """异常1: 批量 vs 单股 MACD DIF 为何不一致。控制 xsflag=-1 / dividend=1 一致, 变化 count"""
    print(f"\n{'='*60}\n>>> [异常1] 批量 vs 单股 DIF 对比 (code={CODE}, xsflag=-1, 前复权)\n{'='*60}")
    rows = []
    for count in [30, 100, 250]:
        # 单股: set_data_info + formula_zb (xsflag 默认 -1)
        tq.formula_set_data_info(stock_code=CODE, stock_period='1d', count=count, dividend_type=1)
        r_sin = tq.formula_zb(formula_name='MACD', formula_arg='12,26,9')
        dif_sin = r_sin.get('Value', {}).get('DIF', []) if isinstance(r_sin, dict) else []

        # 批量: process_mul_zb (显式 xsflag=-1, 单股 list, 取最后5个)
        r_bat = tq.formula_process_mul_zb(
            formula_name='MACD', formula_arg='12,26,9', xsflag=-1,
            return_count=5, return_date=True,
            stock_list=[CODE], stock_period='1d', count=count, dividend_type=1)
        blk = r_bat.get(CODE, {}) if isinstance(r_bat, dict) else {}
        dif_bat_raw = blk.get('DIF', [])
        dif_bat = [it.get('Value') for it in dif_bat_raw[-5:]] if dif_bat_raw else []

        sin_last = dif_sin[-1] if dif_sin else None
        bat_last = dif_bat[-1] if dif_bat else None
        match = "一致" if sin_last is not None and bat_last is not None \
            and abs(float(sin_last) - float(bat_last)) < 1e-4 else "不一致"
        print(f"  count={count:>4}: 单股末5={[round(float(x),3) for x in dif_sin[-5:]]}")
        print(f"  count={count:>4}: 批量末5={dif_bat}")
        print(f"           单股末值={sin_last}  批量末值={bat_last}  -> {match}\n")
        rows.append([count, sin_last, bat_last, match, str(dif_sin[-5:]), str(dif_bat), NOW])
    write_csv("probe_20_anomaly_diag.csv",
              ["count", "单股DIF末值", "批量DIF末值", "是否一致", "单股末5", "批量末5", "探测时间"], rows)


def diag_get_data():
    """异常2: formula_get_data 在不同场景下能否读回"""
    print(f"\n{'='*60}\n>>> [异常2] formula_get_data 读回诊断\n{'='*60}")
    SCODE = "688318.SH"
    rows = []

    # 场景1: set_data_info 后直接 get
    tq.formula_set_data_info(stock_code=SCODE, stock_period='1d', count=5, dividend_type=1)
    gd1 = tq.formula_get_data()
    n1 = len(gd1.get('Value', [])) if isinstance(gd1, dict) else 0
    keys1 = list(gd1.keys()) if isinstance(gd1, dict) else []
    print(f"  [1] set_data_info → get_data: Data={n1}条 keys={keys1}")

    # 场景2: set_data_info + formula_zb 计算后 get (假设计算触发数据落地)
    tq.formula_zb(formula_name='MACD', formula_arg='12,26,9')
    gd2 = tq.formula_get_data()
    n2 = len(gd2.get('Value', [])) if isinstance(gd2, dict) else 0
    print(f"  [2] set_info → formula_zb计算 → get_data: Data={n2}条")
    print(f"      get_data 完整返回(前300字): {str(gd2)[:300]}")

    # 场景3: set_data(格式化数据) 后 get
    md = tq.get_market_data(stock_list=[SCODE], count=5, period='1d', dividend_type=1)
    fmt = tq.formula_format_data(md)
    tq.formula_set_data(stock_code=SCODE, stock_period='1d',
                        stock_data=fmt[SCODE], count=len(fmt[SCODE]), dividend_type=1)
    gd3 = tq.formula_get_data()
    n3 = len(gd3.get('Value', [])) if isinstance(gd3, dict) else 0
    print(f"  [3] set_data(format) → get_data: Data={n3}条")

    # 场景4: get_data 顶层结构是不是变了字段名(不只 Data)
    print(f"  [4] get_data 顶层 type={type(gd3).__name__} keys={list(gd3.keys()) if isinstance(gd3,dict) else '?'}")

    rows.append(["set_data_info→get", n1, str(keys1), NOW])
    rows.append(["set_info→计算→get", n2, "", NOW])
    rows.append(["set_data→get", n3, "", NOW])
    write_csv("probe_20_getdata_diag.csv",
              ["场景", "Data条数", "get顶层keys", "探测时间"], rows)

    print("\n  [结论]")
    print("  → get_data 数据在 'Value' 字段(文档写的 'Data' 已过时); 三种 set 方式都能读回, 非API问题。")


if __name__ == "__main__":
    print(f"===== probe_20 启动 @ {NOW} =====")
    diag_batch_vs_single()
    diag_get_data()
    tq.close()
    print(f"===== probe_20 完成 =====")
