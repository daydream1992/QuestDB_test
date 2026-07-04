# -*- coding: utf-8 -*-
# @meta 接口名称=get_sector_list + get_stock_list_in_sector（板块列表 + 成份股）
# @meta 所属文档=c分类板块/获取A股板块代码列表.md, c分类板块/获取板块成份股.md
# @meta 探测目标=1) 板块总数及类型分布；2) 各板块成份股数量上限；3) 板块 code 字段与其他接口(如 get_relation)的一致性
"""
运行方式:
    python probe_01_sector_list_and_constituent.py

输出:
    probe_01_sector_type_distribution.csv  — 板块按类型分组数量分布
    probe_01_sector_topN_constituents.csv — 板块成份股数量（前 200 个板块作为抽样）
    probe_01_relation_vs_sector_crosscheck.csv — 用若干个股，对比 get_relation 返回的板块 code 与 get_stock_list_in_sector 返回的个股是否双向匹配
"""

import sys
import os
import csv
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tqcenter import tq

# —— 初始化 TQ ——
tq.initialize(__file__)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_outputs")
os.makedirs(OUT_DIR, exist_ok=True)

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_csv(filename, headers, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"[OK] 输出: {path} 共 {len(rows)} 行")


# ============================================================
# 探测 1) 板块列表总数及类型分布
# ============================================================
def probe_sector_list_distribution():
    print("\n>>> [探测1] 调用 get_sector_list —— 取全部板块代码/名称/类型")
    t0 = time.time()
    sectors = tq.get_sector_list(list_type=1)
    cost_ms = int((time.time() - t0) * 1000)

    if not sectors:
        print("[WARN] get_sector_list 未返回数据")
        return

    print(f"    板块总数: {len(sectors)}, 耗时: {cost_ms} ms")

    # 按类型统计
    type_counter = {}
    rows_distribution = []
    # 检查返回字段
    first_keys = list(sectors[0].keys()) if len(sectors) else []
    print(f"    返回字段: {first_keys}")

    for s in sectors:
        code = s.get("Code", "")
        name = s.get("Name", "")
        tp = s.get("Type", "")  # 通达信文档中可能为 Type，有些环境为 Industry/Concept/Area
        if not tp:
            # 兼容字段缺失时，按名称猜测
            tp = s.get("Market", "")
        type_counter[tp] = type_counter.get(tp, 0) + 1
        rows_distribution.append([code, name, tp, NOW, cost_ms])

    write_csv(
        "probe_01_sector_list_all.csv",
        ["板块代码Code", "板块名称Name", "类型Type", "探测时间", "接口耗时ms"],
        rows_distribution,
    )

    # 分布汇总
    dist_rows = [[k, v, NOW] for k, v in type_counter.items()]
    dist_rows.sort(key=lambda r: -r[1])
    write_csv(
        "probe_01_sector_type_distribution.csv",
        ["板块类型", "数量", "探测时间"],
        dist_rows,
    )

    return sectors


# ============================================================
# 探测 2) 取前 N 个板块的成份股，观察数量上限和字段完整性
# ============================================================
def probe_sector_constituents(sectors, top_n=150):
    print(f"\n>>> [探测2] 对前 {top_n} 个板块调用 get_stock_list_in_sector，测试成份股数量上限")
    if not sectors:
        return

    rows = []
    for i, s in enumerate(sectors[:top_n]):
        code = s.get("Code", "")
        name = s.get("Name", "")
        if not code:
            continue

        t0 = time.time()
        try:
            stocks = tq.get_stock_list_in_sector(code, list_type=0)
            count = len(stocks) if isinstance(stocks, list) else 0
            # 抽 3 只个股
            sample = ""
            if stocks and isinstance(stocks, list) and len(stocks):
                top3 = stocks[:3]
                parts = []
                for st in top3:
                    parts.append(f"{st.get('Code','')}:{st.get('Name','')}")
                sample = " | ".join(parts)
            err = ""
        except Exception as e:
            count = -1
            sample = ""
            err = str(e)
        cost_ms = int((time.time() - t0) * 1000)

        rows.append([code, name, count, sample, cost_ms, err, NOW])

        if (i + 1) % 30 == 0:
            print(f"    进度: {i+1}/{top_n} 已探测")

    write_csv(
        "probe_01_sector_topN_constituents.csv",
        ["板块代码", "板块名称", "成份股数", "抽样个股", "耗时ms", "错误", "探测时间"],
        rows,
    )

    # 统计分布
    if rows:
        qty_list = [r[2] for r in rows if isinstance(r[2], int) and r[2] > 0]
        if qty_list:
            qty_min = min(qty_list)
            qty_max = max(qty_list)
            qty_avg = sum(qty_list) // len(qty_list)
            summary_rows = [
                ["板块抽样数", len(qty_list), NOW],
                ["最小成份股数", qty_min, NOW],
                ["最大成份股数", qty_max, NOW],
                ["平均成份股数", qty_avg, NOW],
            ]
            write_csv(
                "probe_01_sector_constituent_summary.csv",
                ["指标", "数值", "探测时间"],
                summary_rows,
            )

    return rows


# ============================================================
# 探测 3) 跨接口一致性：用若干个股，对比 get_relation 返回的板块
# 与 get_stock_list_in_sector(code) 里是否真正包含该股票
# ============================================================
def probe_relation_vs_sector(sectors, sample_stocks=None):
    print("\n>>> [探测3] get_relation vs get_stock_list_in_sector 双向匹配")
    if sample_stocks is None:
        # 如果没指定，就取第一个板块里的前 5 只
        if not sectors:
            return
        first = sectors[0]
        stocks = tq.get_stock_list_in_sector(first.get("Code", ""), list_type=0)
        if not stocks:
            return
        sample_stocks = [st.get("Code", "") for st in stocks[:5]]

    rows = []
    for stk_code in sample_stocks:
        try:
            rel = tq.get_relation(stk_code)
        except Exception as e:
            rel = None
            print(f"    {stk_code} get_relation 失败: {e}")

        if not rel:
            rows.append([stk_code, "[]", "", "N/A", "N/A", "get_relation空", NOW])
            continue

        # 看返回字段
        tp_blocks = []
        for b in rel:
            tp_blocks.append({
                "BlockCode": b.get("BlockCode", ""),
                "BlockName": b.get("BlockName", ""),
                "BlockType": b.get("BlockType", ""),
            })

        # 对返回的每个板块代码，重新取其成份股，检查该股是否在里面
        for b in tp_blocks:
            bc = b["BlockCode"]
            if not bc:
                # 指数/风格板块可能只有名称没有 code
                rows.append([stk_code, b["BlockName"], b["BlockType"], "N/A", "N/A", "板块无code跳过", NOW])
                continue
            try:
                consti = tq.get_stock_list_in_sector(bc, list_type=0)
            except Exception as e:
                rows.append([stk_code, b["BlockName"], b["BlockType"], bc, str(len(consti or [])), f"err:{e}", NOW])
                continue

            const_codes = [c.get("Code", "") for c in (consti or [])]
            hit = stk_code in const_codes
            status = "匹配OK" if hit else "未匹配"
            rows.append([
                stk_code,
                b["BlockName"],
                b["BlockType"],
                bc,
                str(len(const_codes)),
                status,
                NOW,
            ])

    write_csv(
        "probe_01_relation_vs_sector_crosscheck.csv",
        ["股票代码", "板块名称", "板块类型", "板块代码", "板块成份股数", "双向匹配结果", "探测时间"],
        rows,
    )


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print(f"===== probe_01 启动 @ {NOW} =====")
    sectors = probe_sector_list_distribution()
    probe_sector_constituents(sectors, top_n=150)

    # 选 3 个来自不同板块的股票做双向一致性验证
    sample_stocks = []
    if sectors and len(sectors) >= 1:
        # 取三个板块，每板块各抽 1-2 只
        for s in sectors[:3]:
            try:
                stocks = tq.get_stock_list_in_sector(s.get("Code", ""), list_type=0)
                if stocks:
                    for st in stocks[:2]:
                        sample_stocks.append(st.get("Code", ""))
            except Exception:
                continue
    # 去重
    sample_stocks = list(dict.fromkeys(sample_stocks))[:6]
    print(f"    选择用于双向验证的股票: {sample_stocks}")
    probe_relation_vs_sector(sectors, sample_stocks)

    print(f"\n===== probe_01 全部完成 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
