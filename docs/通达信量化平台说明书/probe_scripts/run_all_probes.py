# -*- coding: utf-8 -*-
# @meta 索引脚本：按编号索引
# 执行所有 probe 接口探测脚本（无人值守模式）
"""
运行: python run_all_probes.py
输出: csv_outputs/ 下所有 CSV 文件
"""

import os
import sys
import time
import traceback
from datetime import datetime

PROBES = [
    "probe_01_sector_list_and_constituent.py",
    "probe_02_market_snapshot.py",
    "probe_03_pricevol_vs_snapshot.py",
    "probe_04_gb_info_history.py",
    "probe_05_ipo_info.py",
    "probe_06_more_info_vs_relation.py",
    "probe_07_match_stkinfo.py",
    "probe_08_financial_vs_one.py",
    "probe_09_scjy_value.py",
    "probe_10_gpjy_value.py",
    "probe_11_bkjy_value.py",
    "probe_12_user_sector.py",
    "probe_13_kzz_info.py",
    "probe_14_etf_trackzs.py",
    "probe_15_stock_info_full.py",
    "probe_16_formula_list_info.py",
    "probe_17_formula_single_calc.py",
    "probe_18_formula_batch_mul.py",
    "probe_19_formula_data_cycle.py",
    "probe_20_formula_anomaly_diag.py",
    "probe_21_macd_golden_cross.py",
    "probe_22_sector_workflow.py",
    "probe_23_version_new_apis.py",
    "probe_24_version_logic.py",
]

HERE = os.path.dirname(os.path.abspath(__file__))

def run_one(script):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        print(f"[SKIP] {script} —— 文件不存在")
        return False
    print(f"\n{'=' * 60}\n>>> 开始执行 {script} @ {datetime.now().strftime('%H:%M:%S')}\n{'=' * 60}")
    t0 = time.time()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("probe_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 每个脚本内部通过 if __name__ == "__main__": 块会在加载时执行
        # 但因为我们显式 load，所以需要手动调用主逻辑
        # 由于脚本中 `if __name__ == "__main__":` 不会触发
        # 我们直接通过 subprocess 调用
        import subprocess
        result = subprocess.run(
            [sys.executable, path],
            cwd=HERE, capture_output=True, text=True, timeout=900)
        if result.stdout:
            print(result.stdout[-2000:])
        if result.stderr:
            print("STDERR:", result.stderr[-1000:])
        cost = int(time.time() - t0)
        status = "OK" if result.returncode == 0 else f"FAIL({result.returncode})"
    except Exception as e:
        cost = int(time.time() - t0)
        status = f"ERR:{str(e)}"
        traceback.print_exc()
    print(f"[DONE] {script} —— {status} 耗时 {cost}s")
    return True


if __name__ == "__main__":
    start = time.time()
    print("开始按序执行所有接口探测任务...")
    success = 0
    failed = 0
    for p in PROBES:
        ok = run_one(p)
        if ok:
            success += 1
        else:
            failed += 1
    print(f"\n===== 全部完成: 成功 {success}, 失败 {failed}, 总耗时 {int(time.time() - start)}s =====")
