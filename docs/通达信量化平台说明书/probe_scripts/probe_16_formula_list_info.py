# -*- coding: utf-8 -*-
# @meta 接口名称=formula_get_all / formula_get_info（通达信公式清单与元信息探测）
# @meta 所属文档=f 调用通达信公式/获取指定种类的公式列表.md + 获取指定公式信息.md
# @meta 探测目标=1) 三类公式(技术指标/选股/专家系统)清单数量与采样; 2) 典型公式参数定义(MACD/KDJ/BOLL/UPN/CCI); 3) isSys区分内置vs自定义
"""
运行: python probe_16_formula_list_info.py
输出: csv_outputs/probe_16_formula_list.csv + probe_16_formula_info.csv
依赖: 已登录通达信客户端
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


# 公式类型: 0=技术指标 1=条件选股 2=专家系统 (对应 formula_zb/xg/exp)
FORMULA_TYPES = {0: "技术指标", 1: "条件选股", 2: "专家系统"}
# 典型公式元信息探测 (type, code, 期望参数名)
PROBE_FORMULAS = [
    (0, "MACD", ["SHORT", "LONG", "MID"]),
    (0, "KDJ", ["N", "M1", "M2"]),
    (0, "BOLL", ["N"]),
    (1, "UPN", ["N"]),
    (2, "CCI", ["N"]),
]


def probe_list():
    print("\n>>> [探测1] 三类公式清单 formula_get_all")
    rows = []
    for t, tname in FORMULA_TYPES.items():
        t0 = time.time()
        err = ""
        try:
            r = tq.formula_get_all(formula_type=t)
        except Exception as e:  # noqa: BLE001
            r = None
            err = str(e)[:80]
        cost = int((time.time() - t0) * 1000)
        n = len(r) if isinstance(r, (list, dict)) else 0
        sample = r[:5] if isinstance(r, list) else (list(r.items())[:5] if isinstance(r, dict) else [])
        rows.append([t, tname, n, str(sample)[:200], err, cost, NOW])
        print(f"    type={t} {tname}: {n} 个 ({cost}ms) {err}")
    write_csv("probe_16_formula_list.csv",
              ["类型代码", "类型名", "公式数", "前5采样", "错误", "耗时ms", "探测时间"], rows)


def probe_info():
    print("\n>>> [探测2] 典型公式元信息 formula_get_info")
    rows = []
    for t, code, expect in PROBE_FORMULAS:
        t0 = time.time()
        err = ""
        try:
            r = tq.formula_get_info(formula_type=t, formula_code=code)
        except Exception as e:  # noqa: BLE001
            r = None
            err = str(e)[:80]
        cost = int((time.time() - t0) * 1000)
        if isinstance(r, dict) and r:
            name = r.get("acName", "")
            is_sys = r.get("isSys", "")
            para_num = r.get("ParaNum", "")
            paras = r.get("Para", [])
            para_str = "; ".join(
                f"{p.get('ParaName')}={p.get('Default')}(范围{p.get('Min')}~{p.get('Max')})"
                for p in paras
            ) if isinstance(paras, list) else ""
            rows.append([t, FORMULA_TYPES[t], code, name,
                         "内置" if str(is_sys) == "1" else f"自定义(isSys={is_sys})",
                         para_num, para_str, "", cost, NOW])
            print(f"    {code}({name}): {'内置' if str(is_sys)=='1' else '自定义'} ParaNum={para_num} ({cost}ms)")
        else:
            rows.append([t, FORMULA_TYPES[t], code, "FAIL/空", "", "", "", err, cost, NOW])
            print(f"    {code}: FAIL {err} ({cost}ms)")
    write_csv("probe_16_formula_info.csv",
              ["类型代码", "类型名", "公式代码", "公式名", "内置/自定义",
               "参数个数", "参数定义", "错误", "耗时ms", "探测时间"], rows)


if __name__ == "__main__":
    print(f"===== probe_16 启动 @ {NOW} =====")
    probe_list()
    probe_info()
    tq.close()
    print(f"===== probe_16 完成 =====")
