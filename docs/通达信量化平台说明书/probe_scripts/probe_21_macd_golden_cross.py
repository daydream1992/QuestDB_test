# -*- coding: utf-8 -*-
# @meta 场景=MACD金叉全市场选股（g场景化例子/打通...双向数据互通闭环.md 批量版）
# @meta 核心 API=formula_process_mul_zb(MACD) + 金叉判定逻辑
# @meta 探测目标=1) 批量MACD选股链路; 2) 金叉判定(DIF[-2]<DEA[-2] 且 DIF[-1]>=DEA[-1]); 3) 应用probe_20结论count>=100暖机
"""
运行: python probe_21_macd_golden_cross.py [--limit 200] [--top 20]
输出: csv_outputs/probe_21_macd_golden_cross.csv
说明: 纯读, 不写板块(send_user_block 不调用, 仅打印金叉列表)。
      ⚠️ 文章 for循环版用 macd_result['Data']['DIF'] 是过时的(formula_zb 返回 Value 非 Data),
      本探针用批量版结构 res[code]['DIF'] 直接取(已验证正确)。
"""
import sys
import os
import csv
import time
import argparse
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


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=200, help='股票池大小(前N只, 默认200)')
    p.add_argument('--top', type=int, default=20, help='显示前N名')
    return p.parse_args()


def main():
    args = parse_args()
    print(f"\n>>> MACD金叉选股 (股票池前{args.limit}只, count=100保证暖机)")

    # 1. 股票池
    try:
        all_stocks = tq.get_stock_list(market='5')
    except Exception as e:  # noqa: BLE001
        print(f"FAIL get_stock_list: {e}")
        return
    stocks = all_stocks[:args.limit]
    print(f"    股票池: {len(stocks)} 只")

    # 2. 批量算MACD (count=100, probe_20结论: 趋势指标需>=100才收敛)
    t0 = time.time()
    try:
        res = tq.formula_process_mul_zb(
            formula_name='MACD', formula_arg='12,26,9', xsflag=6,
            return_count=2, return_date=False,
            stock_list=stocks, stock_period='1d', count=100, dividend_type=1)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL formula_process_mul_zb: {e}")
        return
    cost = int((time.time() - t0) * 1000)
    eid = res.get('ErrorId', '?') if isinstance(res, dict) else '?'
    print(f"    批量MACD: ErrorId={eid} ({cost}ms)")

    # 3. 筛金叉: DIF[-2]<DEA[-2] 且 DIF[-1]>=DEA[-1]
    crosses = []
    for code in stocks:
        blk = res.get(code, {}) if isinstance(res, dict) else {}
        dif = blk.get('DIF', [])
        dea = blk.get('DEA', [])
        if len(dif) < 2 or len(dea) < 2:
            continue
        try:
            d_prev, d_now = float(dif[-2]), float(dif[-1])
            e_prev, e_now = float(dea[-2]), float(dea[-1])
            if d_prev < e_prev and d_now >= e_now:
                crosses.append([code, d_now, e_now, d_now - e_now, NOW])
        except (TypeError, ValueError):
            continue

    print(f"\n    🔥 MACD金叉股票: {len(crosses)} 只 (占池 {len(crosses)}/{len(stocks)}={100*len(crosses)//max(1,len(stocks))}%)")
    if crosses:
        crosses.sort(key=lambda x: -float(x[3]))  # 按DIF-DEA差值降序
        print(f"    TOP{args.top}(按DIF-DEA差值降序):")
        for i, row in enumerate(crosses[:args.top], 1):
            print(f"      {i}. {row[0]}: DIF={row[1]:.3f} DEA={row[2]:.3f} (差{row[3]:+.3f})")

    write_csv("probe_21_macd_golden_cross.csv",
              ["代码", "DIF", "DEA", "DIF-DEA", "探测时间"],
              crosses if crosses else [["(无金叉)", "", "", "", NOW]])
    tq.close()


if __name__ == "__main__":
    print(f"===== probe_21 启动 @ {NOW} =====")
    main()
    print(f"===== probe_21 完成 =====")
