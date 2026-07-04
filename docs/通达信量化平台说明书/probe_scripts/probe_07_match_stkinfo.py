# -*- coding: utf-8 -*-
# @meta 接口名称=get_match_stkinfo（证券信息模糊检索）
# @meta 所属文档=通用函数/检索证券信息.md
# @meta 探测目标=1) 不同关键字类型（股票名/代码/拼音首字母/行业名）命中率；2) max_count 参数生效；3) 重复调用稳定性
"""
运行: python probe_07_match_stkinfo.py
输出:
    probe_07_match_keyword_hit.csv —— 关键字命中率矩阵
    probe_07_match_maxcount_effect.csv —— max_count 参数测试
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


KEYWORDS = [
    ("名称", "茅台"),
    ("名称", "宁德时代"),
    ("名称", "平安"),
    ("代码", "600519"),
    ("代码", "000001"),
    ("代码", "688318"),
    ("拼音", "mt"),
    ("拼音", "ndsd"),
    ("行业", "银行"),
    ("行业", "半导体"),
    ("行业", "新能源"),
    ("概念", "华为"),
    ("地名", "深圳"),
]


def probe_keyword_hit_rate():
    print("\n>>> [探测1] 关键字命中率矩阵")
    rows = []
    for kind, kw in KEYWORDS:
        t0 = time.time()
        try:
            res = tq.get_match_stkinfo(kw, max_count=200)
            n = len(res) if isinstance(res, list) else -1
            top5 = ";".join([f"{x.get('Code','')}:{x.get('Name','')}" for x in (res[:5] if isinstance(res, list) else [])])
            err = ""
        except Exception as e:
            n = -1
            top5 = ""
            err = str(e)
        cost_ms = int((time.time() - t0) * 1000)
        rows.append([kind, kw, n, top5, cost_ms, err, NOW])

    write_csv(
        "probe_07_match_keyword_hit.csv",
        ["关键字类型", "关键字", "返回条数", "前5命中", "耗时ms", "错误", "探测时间"],
        rows,
    )


def probe_max_count():
    print("\n>>> [探测2] max_count 参数控制（1/5/20/100/500）")
    rows = []
    kw = "平安"
    for mc in [1, 5, 20, 50, 100, 200, 500, 1000]:
        try:
            res = tq.get_match_stkinfo(kw, max_count=mc)
            n = len(res) if isinstance(res, list) else -1
            sample = str(res[0]) if isinstance(res, list) and res else ""
            err = ""
        except Exception as e:
            n = -1
            sample = ""
            err = str(e)
        rows.append([kw, mc, n, sample[:200], err, NOW])
    write_csv(
        "probe_07_match_maxcount_effect.csv",
        ["关键字", "max_count", "实际返回数", "第一个样例", "错误", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_07 启动 @ {NOW} =====")
    probe_keyword_hit_rate()
    probe_max_count()
    print(f"===== probe_07 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
