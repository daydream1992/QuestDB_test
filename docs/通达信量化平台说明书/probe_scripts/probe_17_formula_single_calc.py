# -*- coding: utf-8 -*-
# @meta 接口名称=formula_set_data_info / formula_zb / formula_xg / formula_exp（单股公式计算流程）
# @meta 所属文档=f 调用通达信公式/调用通达信公式进行计算.md + 向通达信公式设置数据信息.md
# @meta 探测目标=1) set_data_info→zb/xg/exp 单股三步流程; 2) MACD指标/UPN选股/CCI专家系统三类返回; 3) 多市场股票覆盖
"""
运行: python probe_17_formula_single_calc.py
输出: csv_outputs/probe_17_formula_single_calc.csv
说明: 单股公式须先 formula_set_data_info 设置数据上下文, 再 formula_zb/xg/exp 计算;
      每只票需重新 set(后设覆盖前设)。
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


def last_value(value_dict, key):
    """从单股公式返回 {'Value': {key: [...]}} 取最后一个非None值"""
    if not isinstance(value_dict, dict):
        return None
    lst = value_dict.get(key, [])
    if isinstance(lst, list):
        for v in reversed(lst):
            if v is not None:
                return v
    return None


def probe_single():
    print("\n>>> 单股公式流程: set_data_info → formula_zb/xg/exp")
    rows = []
    for code in TEST_STOCKS:
        market = code.split(".")[-1]
        t0 = time.time()
        row = [code, market]

        # 1. 设置数据上下文(每只票重设,覆盖前只)
        try:
            tq.formula_set_data_info(
                stock_code=code, stock_period='1d', count=30, dividend_type=1)
            set_ok = "OK"
        except Exception as e:  # noqa: BLE001
            set_ok = f"FAIL:{str(e)[:30]}"
        row.append(set_ok)

        # 2. MACD 技术指标公式 (formula_zb, type=0)
        try:
            r = tq.formula_zb(formula_name='MACD', formula_arg='12,26,9')
            v = r.get('Value', {}) if isinstance(r, dict) else {}
            macd_dif = last_value(v, 'DIF')
            macd_macd = last_value(v, 'MACD')
        except Exception as e:  # noqa: BLE001
            macd_dif = macd_macd = f"ERR:{str(e)[:25]}"
        row += [macd_dif, macd_macd]

        # 3. UPN 条件选股公式 (formula_xg, type=1) — 复用同股已set的数据
        try:
            r = tq.formula_xg(formula_name='UPN', formula_arg='3')
            v = r.get('Value', {}) if isinstance(r, dict) else {}
            upn = last_value(v, 'UP3')
        except Exception as e:  # noqa: BLE001
            upn = f"ERR:{str(e)[:25]}"
        row.append(upn)

        # 4. CCI 专家系统公式 (formula_exp, type=2)
        try:
            r = tq.formula_exp(formula_name='CCI', formula_arg='12')
            v = r.get('Value', {}) if isinstance(r, dict) else {}
            enter = last_value(v, 'ENTERLONG')
            exit_ = last_value(v, 'EXITLONG')
        except Exception as e:  # noqa: BLE001
            enter = exit_ = f"ERR:{str(e)[:25]}"
        row += [enter, exit_]

        cost = int((time.time() - t0) * 1000)
        row += [cost, NOW]
        rows.append(row)
        print(f"    {code}: MACD_DIF={macd_dif} UPN={upn} CCI_enter={enter} ({cost}ms)")

    write_csv("probe_17_formula_single_calc.csv",
              ["代码", "市场", "set_data_info", "MACD_DIF", "MACD_MACD",
               "UPN(UP3)", "CCI_ENTERLONG", "CCI_EXITLONG", "耗时ms", "探测时间"], rows)


if __name__ == "__main__":
    print(f"===== probe_17 启动 @ {NOW} =====")
    probe_single()
    tq.close()
    print(f"===== probe_17 完成 =====")
