# -*- coding: utf-8 -*-
# @meta 接口名称=get_ipo_info（新股申购信息）
# @meta 所属文档=a行情类信息/获取新股申购信息.md
# @meta 探测目标=1) ipo_type=0/1/2 分别获取的内容对比；2) ipo_date=0/1 两种模式差异；3) 字段覆盖率
"""
运行: python probe_05_ipo_info.py
输出:
    probe_05_ipo_info_types.csv —— ipo_type 0/1/2 交叉对比
    probe_05_ipo_info_date_mode.csv —— ipo_date 0 vs 1
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


IPO_TYPE_LABEL = {0: "仅新股申购", 1: "仅新发债", 2: "新股+新发债"}
IPO_DATE_LABEL = {0: "仅当日", 1: "今日及之后"}


def probe_ipo_types():
    print("\n>>> [探测1] ipo_type 三模式对比")
    all_merged = {}  # code -> row dict
    headers_for_all = set()
    for itype in [0, 1, 2]:
        t0 = time.time()
        try:
            res = tq.get_ipo_info(ipo_type=itype, ipo_date=1)
            if isinstance(res, list):
                n = len(res)
            else:
                n = -1
            cost_ms = int((time.time() - t0) * 1000)
            print(f"    ipo_type={itype} ({IPO_TYPE_LABEL[itype]}): {n} 条, {cost_ms}ms")
            if isinstance(res, list):
                for item in res:
                    code = item.get("Code", "")
                    all_merged.setdefault(code, {})[f"type{itype}_name"] = item.get("Name", "")
                    all_merged[code][f"type{itype}_SGDate"] = item.get("SGDate", "")
                    all_merged[code][f"type{itype}_SGCode"] = item.get("SGCode", "")
                    all_merged[code][f"type{itype}_MaxSG"] = str(item.get("MaxSG", ""))
                    all_merged[code][f"type{itype}_PE_Issue"] = str(item.get("PE_Issue", ""))
                    for k in item.keys():
                        headers_for_all.add(k)
        except Exception as e:
            print(f"    ipo_type={itype} 异常: {e}")

    # 合并输出
    if all_merged:
        col_names = [
            "Code",
            "type0_name", "type0_SGDate", "type0_SGCode", "type0_MaxSG", "type0_PE_Issue",
            "type1_name", "type1_SGDate", "type1_SGCode", "type1_MaxSG", "type1_PE_Issue",
            "type2_name", "type2_SGDate", "type2_SGCode", "type2_MaxSG", "type2_PE_Issue",
            "探测时间",
        ]
        rows = []
        for code in sorted(all_merged.keys()):
            row = all_merged[code]
            rows.append([
                code,
                row.get("type0_name", ""), row.get("type0_SGDate", ""),
                row.get("type0_SGCode", ""), row.get("type0_MaxSG", ""), row.get("type0_PE_Issue", ""),
                row.get("type1_name", ""), row.get("type1_SGDate", ""),
                row.get("type1_SGCode", ""), row.get("type1_MaxSG", ""), row.get("type1_PE_Issue", ""),
                row.get("type2_name", ""), row.get("type2_SGDate", ""),
                row.get("type2_SGCode", ""), row.get("type2_MaxSG", ""), row.get("type2_PE_Issue", ""),
                NOW,
            ])
        write_csv("probe_05_ipo_info_types.csv", col_names, rows)
    print(f"    发现唯一 Code 数: {len(all_merged)}; 返回字段集: {sorted(headers_for_all)}")


def probe_ipo_date_mode():
    print("\n>>> [探测2] ipo_date=0 vs 1")
    rows = []
    for itype in [0, 1, 2]:
        for idate in [0, 1]:
            t0 = time.time()
            try:
                res = tq.get_ipo_info(ipo_type=itype, ipo_date=idate)
                n = len(res) if isinstance(res, list) else -1
                names = [x.get("Name", "") for x in (res if isinstance(res, list) else [])][:5]
                err = ""
            except Exception as e:
                n = -1
                names = []
                err = str(e)
            cost_ms = int((time.time() - t0) * 1000)
            rows.append([
                itype, IPO_TYPE_LABEL[itype],
                idate, IPO_DATE_LABEL[idate],
                n, "|".join(names), cost_ms, err, NOW,
            ])

    write_csv(
        "probe_05_ipo_info_date_mode.csv",
        ["ipo_type", "type说明", "ipo_date", "date说明", "返回条数", "前5名称样", "耗时ms", "错误", "探测时间"],
        rows,
    )


if __name__ == "__main__":
    print(f"===== probe_05 启动 @ {NOW} =====")
    probe_ipo_types()
    probe_ipo_date_mode()
    print(f"===== probe_05 完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
