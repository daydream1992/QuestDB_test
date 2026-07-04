# -*- coding: utf-8 -*-
# @meta 场景=版本更新探测①：新市场后缀(.CSI/.CFF/.HG/.QHZ) + 新增函数(get_relation/exec_to_tdx/formula_process_mul_exp)
# @meta 来源=tqcenter 版本更新日志(新增中证指数/中金所期货/宏观数据/期货指数后缀, 新增 get_relation/exec_to_tdx)
# @meta 探测目标=1) 新后缀代码能否被 get_more_info 识别取数; 2) get_relation 返回所属板块结构; 3) exec_to_tdx 调用(DRY); 4) formula_process_mul_exp 批量专家系统
"""
运行: python probe_23_version_new_apis.py
输出: csv_outputs/probe_23_version_new_apis.csv
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


# 新市场后缀代表代码(部分推测, 会先用 get_match_stkinfo 搜真实代码)
NEW_SUFFIX_CODES = {
    "CSI": ["000300.CSI", "000905.CSI", "000852.CSI"],   # 中证指数(沪深300/中证500/中证1000)
    "CFF": ["IF2506.CFF", "IC2506.CFF", "IM2506.CFF"],   # 中金所期货(股指期货, 推测合约)
    "HG":  ["CPI.HG", "GDP.HG", "PMI.HG"],               # 宏观数据(推测)
    "QHZ": ["IF主连.QHZ", "螺纹钢主连.QHZ"],              # 期货指数(推测)
}


def probe_new_markets(rows):
    print(f"\n>>> [1] 新市场后缀代码识别 (get_match_stkinfo 搜真实代码 + get_more_info 取数)")
    # 1a. 搜索关键词, 收集真实新后缀代码
    found = {}
    for kw in ["沪深300", "中证1000", "螺纹钢", "股指期货", "CPI"]:
        try:
            r = tq.get_match_stkinfo(key_word=kw)
            if isinstance(r, list):
                for it in r[:15]:
                    if isinstance(it, dict) and it.get("Code"):
                        suffix = it["Code"].split(".")[-1] if "." in it["Code"] else ""
                        if suffix in NEW_SUFFIX_CODES:
                            found.setdefault(suffix, set()).add(it["Code"])
        except Exception:  # noqa: BLE001
            pass
    found = {k: list(v)[:3] for k, v in found.items()}
    print(f"    搜索到的真实新后缀代码: {found if found else '(无, 用推测代码测)'}")

    # 1b. 测候选代码 + 搜到的真实代码
    test_set = []
    for suffix, codes in NEW_SUFFIX_CODES.items():
        test_set += [(suffix, c) for c in codes]
        test_set += [(suffix, c) for c in found.get(suffix, [])]
    seen = set()
    for suffix, code in test_set:
        if code in seen:
            continue
        seen.add(code)
        t0 = time.time()
        try:
            info = tq.get_more_info(stock_code=code)
            cost = int((time.time() - t0) * 1000)
            ok = isinstance(info, dict) and info.get("ErrorId", "0") == "0" and len(info) > 1
            name = info.get("Name", "") if isinstance(info, dict) else ""
            print(f"    .{suffix} {code}: {'✅OK' if ok else '❌空'} Name={name!r} ({cost}ms)")
            rows.append([f".{suffix}", code, "OK" if ok else "空/失败", name, cost, NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    .{suffix} {code}: ERR {str(e)[:50]}")
            rows.append([f".{suffix}", code, "ERR", str(e)[:50], 0, NOW])


def probe_get_relation(rows):
    print(f"\n>>> [2] get_relation(stock_code) 股票所属板块")
    for code in ["600519.SH", "000001.SZ", "300750.SZ"]:
        try:
            r = tq.get_relation(stock_code=code)
            n = len(r) if isinstance(r, list) else 0
            sample = r[:3] if isinstance(r, list) else str(r)[:100]
            print(f"    {code}: {n}个板块, 样本={sample}")
            rows.append(["get_relation", code, n, str(sample)[:100], NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    {code}: ERR {e}")
            rows.append(["get_relation", code, "ERR", str(e)[:60], NOW])


def probe_exec_to_tdx(rows):
    print(f"\n>>> [3] exec_to_tdx(url) 客户端功能调用 (DRY: 不传危险url)")
    for url in ["", "about:blank"]:
        try:
            r = tq.exec_to_tdx(url=url)
            print(f"    exec_to_tdx(url={url!r}): {str(r)[:120]}")
            rows.append(["exec_to_tdx", url, str(r)[:100], NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    ERR {e}")
            rows.append(["exec_to_tdx", url, f"ERR:{str(e)[:60]}", NOW])


def probe_formula_mul_exp(rows):
    print(f"\n>>> [4] formula_process_mul_exp 批量专家系统公式 (CCI)")
    try:
        r = tq.formula_process_mul_exp(
            formula_name="CCI", formula_arg="12",
            return_count=1, return_date=True,
            stock_list=["600519.SH", "000001.SZ"],
            stock_period="1d", count=100, dividend_type=1)
        eid = r.get("ErrorId", "?") if isinstance(r, dict) else "?"
        print(f"    ErrorId={eid}")
        for code in ["600519.SH", "000001.SZ"]:
            blk = r.get(code, {}) if isinstance(r, dict) else {}
            keys = list(blk.keys()) if isinstance(blk, dict) else []
            print(f"    {code}: 输出keys={keys}")
            rows.append(["formula_mul_exp", code, eid, str(keys), NOW])
    except Exception as e:  # noqa: BLE001
        print(f"    ERR {e}")


def main():
    rows = []
    probe_new_markets(rows)
    write_csv("probe_23_new_markets.csv",
              ["后缀", "代码", "结果", "Name", "耗时ms", "时间"],
              rows[:50])  # new_markets 部分单独存
    rows2 = []
    probe_get_relation(rows2)
    probe_exec_to_tdx(rows2)
    probe_formula_mul_exp(rows2)
    write_csv("probe_23_version_new_apis.csv",
              ["探测项", "代码/参数", "结果", "样本", "时间"], rows2)
    tq.close()


if __name__ == "__main__":
    print(f"===== probe_23 启动 @ {NOW} =====")
    main()
    print(f"===== probe_23 完成 =====")
