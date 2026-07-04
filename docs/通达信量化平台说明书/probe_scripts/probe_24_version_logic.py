# -*- coding: utf-8 -*-
# @meta 场景=版本更新探测②：增强逻辑(count=-1+start/end, get_match_stkinfo全品种, 880096-098成份股, 未上市代码容错)
# @meta 来源=tqcenter 版本更新日志
# @meta 探测目标=1) formula_process_mul count=-1 按 start/end 返回; 2) get_match_stkinfo 全品种覆盖; 3) 880096/097/098 成份股; 4) get_market_data 未上市代码容错
"""
运行: python probe_24_version_logic.py
输出: csv_outputs/probe_24_version_logic.csv
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


def probe_count_neg1_startend(rows):
    """count=-1 + start/end 应返回区间内结果(vs count>0 截取)"""
    print(f"\n>>> [1] formula_process_mul_zb count=-1 + start/end (MACD)")
    start, end = "20260601", "20260620"
    for desc, kwargs in [
        ("count=-1+start/end", dict(count=-1, start_time=start, end_time=end)),
        ("count=50(对照)",     dict(count=50)),
    ]:
        try:
            r = tq.formula_process_mul_zb(
                formula_name="MACD", formula_arg="12,26,9", xsflag=4,
                return_count=0, return_date=True,
                stock_list=["600519.SH"], stock_period="1d",
                dividend_type=1, **kwargs)
            blk = r.get("600519.SH", {}) if isinstance(r, dict) else {}
            dif = blk.get("DIF", [])
            n = len(dif)
            dates = [it.get("Date") for it in dif[:3]] + ["..."] + [it.get("Date") for it in dif[-2:]] if n else []
            print(f"    {desc}: DIF返回{n}条, 日期范围={dates}")
            rows.append(["count_test", desc, n, str(dates)[:100], NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    {desc}: ERR {e}")
            rows.append(["count_test", desc, "ERR", str(e)[:60], NOW])


def probe_match_all_types(rows):
    """get_match_stkinfo 全品种支持: 搜不同品种看后缀分布"""
    print(f"\n>>> [2] get_match_stkinfo 全品种 (看返回代码后缀分布)")
    for kw in ["银行", "国债", "螺纹钢", "沪深300", "可转债"]:
        try:
            r = tq.get_match_stkinfo(key_word=kw)
            if isinstance(r, list) and r:
                suffixes = {}
                for it in r:
                    if isinstance(it, dict) and it.get("Code"):
                        sfx = it["Code"].split(".")[-1] if "." in it["Code"] else "?"
                        suffixes[sfx] = suffixes.get(sfx, 0) + 1
                print(f"    {kw!r}: {len(r)}条, 后缀分布={suffixes}")
                rows.append(["match_stkinfo", kw, len(r), str(suffixes)[:100], NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    {kw!r}: ERR {e}")


def probe_sector_880(rows):
    """880096/097/098 系统行业板块成份股(用 block_type=0; 自定义板块才用 bt=1)"""
    print(f"\n>>> [3] 880096/097/098 成份股 (系统板块 block_type=0)")
    for sec in ["880096", "880097", "880098"]:
        try:
            r = tq.get_stock_list_in_sector(block_code=sec, block_type=0)
            n = len(r) if r else 0
            print(f"    {sec}(bt=0): {n}只 成份股, 样本={r[:3] if r else []}")
            rows.append(["sector_880", sec, n, str(r[:3]) if r else "", NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    {sec}: ERR {e}")


def probe_unlisted_toleration(rows):
    """get_market_data 传未上市代码不再报错(changelog修复点)"""
    print(f"\n>>> [4] get_market_data 未上市/非法代码容错 (混合正常+非法)")
    mixed = ["600519.SH", "999999.XX", "000000.SZ"]  # 正常 + 编造
    try:
        r = tq.get_market_data(stock_list=mixed, count=5, period='1d', dividend_type=1)
        if isinstance(r, dict) and "Close" in r:
            df = r["Close"]
            cols = list(df.columns) if hasattr(df, "columns") else []
            print(f"    混合代码未报错, 返回列={cols}")
            rows.append(["unlisted_toleration", str(mixed), "未报错", str(cols), NOW])
        else:
            print(f"    返回结构: {list(r.keys()) if isinstance(r,dict) else type(r).__name__}")
            rows.append(["unlisted_toleration", str(mixed), "返回异常", str(r)[:80], NOW])
    except Exception as e:  # noqa: BLE001
        print(f"    ❌ 仍报错: {e}")
        rows.append(["unlisted_toleration", str(mixed), "报错", str(e)[:80], NOW])


def main():
    rows = []
    probe_count_neg1_startend(rows)
    probe_match_all_types(rows)
    probe_sector_880(rows)
    probe_unlisted_toleration(rows)
    write_csv("probe_24_version_logic.csv",
              ["探测项", "参数", "结果", "样本", "时间"], rows)
    tq.close()


if __name__ == "__main__":
    print(f"===== probe_24 启动 @ {NOW} =====")
    main()
    print(f"===== probe_24 完成 =====")
