# -*- coding: utf-8 -*-
# @meta 场景=选股→加入自定义板块完整工作流（g场景化例子/执行选股策略并加入客户端自定义板块.md）
# @meta 核心 API=create_sector / send_user_block / get_stock_list_in_sector / delete_sector
# @meta 探测目标=1) 板块CRUD完整链路; 2) 修正文章3处参数错误(stock_list非stocks/block_code非sector_code); 3) DRY_RUN默认 + --write必清理
"""
运行: python probe_22_sector_workflow.py             # DRY_RUN 只打印不实写(默认, 安全)
      python probe_22_sector_workflow.py --write     # 真执行(创建临时板块 PROBE_T, finally 必 delete 清理)
输出: csv_outputs/probe_22_sector_workflow.csv
说明: 写操作会改客户端板块状态, 默认 DRY_RUN。--write 用临时板块 PROBE_T, finally 块保证必清理。
"""
import sys
import os
import csv
import argparse
from datetime import datetime

sys.path.append(r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq  # noqa: E402

tq.initialize(__file__)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_outputs")
os.makedirs(OUT_DIR, exist_ok=True)
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

BLOCK_CODE = "PROBE_T"                       # 临时测试板块(绝不复用用户现有板块)
BLOCK_NAME = "探针临时板块(自动清理)"
TEST_STOCKS = ["600519.SH", "000001.SZ", "688318.SH"]


def write_csv(filename, headers, rows):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([headers] + rows)
    print(f"[OK] {path} ({len(rows)} 行)")


def main():
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument('--write', action='store_true', help='真执行写操作(默认DRY_RUN)')
    args = p.parse_args()
    write = args.write

    rows = []
    mode = "WRITE(真写, finally必删)" if write else "DRY_RUN(只打印)"
    print(f"\n模式: {mode} | 临时板块: {BLOCK_CODE}({BLOCK_NAME})")

    try:
        # [1] 创建板块
        print(f"\n>>> [1] create_sector(block_code, block_name) 都必填")
        if write:
            try:
                r = tq.create_sector(block_code=BLOCK_CODE, block_name=BLOCK_NAME)
                print(f"    -> {str(r)[:100]}")
                rows.append(["create_sector", "WRITE", str(r)[:100], NOW])
            except Exception as e:  # noqa: BLE001
                print(f"    ERR {e}"); rows.append(["create_sector", "ERR", str(e)[:80], NOW])
        else:
            msg = f"create_sector(block_code={BLOCK_CODE!r}, block_name={BLOCK_NAME!r})"
            print(f"    [DRY] {msg}"); rows.append(["create_sector", "DRY", msg, NOW])

        # [2] 加入股票 (⚠️ 参数是 stock_list 非 stocks)
        print(f"\n>>> [2] send_user_block(block_code, stock_list, show)  ⚠️stock_list非stocks")
        if write:
            try:
                r = tq.send_user_block(block_code=BLOCK_CODE, stock_list=TEST_STOCKS, show=False)
                print(f"    -> {str(r)[:100]}")
                rows.append(["send_user_block", "WRITE", str(r)[:100], NOW])
            except Exception as e:  # noqa: BLE001
                print(f"    ERR {e}"); rows.append(["send_user_block", "ERR", str(e)[:80], NOW])
        else:
            msg = f"send_user_block(block_code={BLOCK_CODE!r}, stock_list={TEST_STOCKS})"
            print(f"    [DRY] {msg}"); rows.append(["send_user_block", "DRY", msg, NOW])

        # [3] 读回成分股 (读操作; ⚠️ block_code 非 sector_code; 自定义板块需 block_type=1, bt=0只读系统板块)
        print(f"\n>>> [3] get_stock_list_in_sector(block_code, block_type=1)  自定义板块需bt=1")
        try:
            stocks_in = tq.get_stock_list_in_sector(block_code=BLOCK_CODE, block_type=1)
            print(f"    -> 成分股({len(stocks_in) if stocks_in else 0}只): {stocks_in}")
            rows.append(["get_stock_list_in_sector", "READ", str(stocks_in)[:100], NOW])
        except Exception as e:  # noqa: BLE001
            print(f"    ERR {e}"); rows.append(["get_stock_list_in_sector", "ERR", str(e)[:80], NOW])

    finally:
        # [4] 必清理 (finally 保证即使中间失败也删除)
        print(f"\n>>> [4] delete_sector (finally 清理)")
        if write:
            try:
                r = tq.delete_sector(block_code=BLOCK_CODE)
                print(f"    -> 已删除 {str(r)[:100]}")
                rows.append(["delete_sector", "WRITE(清理)", str(r)[:100], NOW])
            except Exception as e:  # noqa: BLE001
                print(f"    ERR {e}"); rows.append(["delete_sector", "ERR", str(e)[:80], NOW])
        else:
            print(f"    [DRY] 未真创建, 无需清理")
            rows.append(["delete_sector", "DRY", "未创建无需清理", NOW])

    write_csv("probe_22_sector_workflow.csv",
              ["步骤", "模式", "结果", "时间"], rows)
    tq.close()


if __name__ == "__main__":
    print(f"===== probe_22 启动 @ {NOW} =====")
    main()
    print(f"===== probe_22 完成 =====")
