"""testv10.2 meso 雷达字段+计时探测 (Phase 0: G5+G6 合并实战版)

脚本路径: K:/QuestDB_test/testv10.2/_probe_meso_fields.py
目的: 在真实 monitor 板块上实测 get_more_info + get_market_snapshot, 回答两个 go/no-go:
  - G5 字段: ZAF/ZTGPNum/fLianB/BCancel/SCancel 等探照灯字段 key 是否存在 + 值非空
            (BCancel 决定模型2 主路径 vs FCAmo 流失率 fallback)
  - G6 计时: 单板块双调用耗时 → 外推 578 板块是否 <30s 预算
依赖: lib/tq_client (safe_call), mapping_store (取真实板块样本)
跑法: python testv10.2/_probe_meso_fields.py [--n 20]
注: 盘前数据为昨日收盘值 (ZAF 多为 0), 本探测定 "字段结构可用性", 实时值质量留盘中再验。
"""

import argparse
import time

import bootstrap
bootstrap.ensure_paths()

from lib.tq_client import safe_call, init, close  # noqa: E402
from tqcenter import tq  # noqa: E402
import mapping_store  # noqa: E402
import settings as cfg  # noqa: E402

# 探照灯关心的字段 (more_info 为主, snapshot 补 UpHome/DownHome)
FIELDS = ['ZAF', 'ZTGPNum', 'fLianB', 'vzangsu', 'Zjl', 'FzAmo',
          'BCancel', 'SCancel', 'UpHome', 'DownHome']


def _is_present(v) -> bool:
    """key 在且值非 None/空串 (0 视为合法值, 如 ZTGPNum=0=无涨停)。"""
    if v is None:
        return False
    if isinstance(v, str) and v == '':
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--n', type=int, default=20, help='抽样板块数 (均分概念/三级)')
    args = parser.parse_args()

    ms = mapping_store.MappingStore(cfg.MAPPING_PARQUET)
    all_boards = ms.boards(cfg.MONITOR_LEVELS)
    concepts = [b for b in all_boards if b['level'] == '概念']
    l3 = [b for b in all_boards if b['level'] == '三级']
    half = max(1, args.n // 2)
    sample = concepts[:half] + l3[:half]
    print(f'宇宙: {len(all_boards)} monitor 板块 (概念 {len(concepts)} / 三级 {len(l3)})')
    print(f'抽样: {len(sample)} 板块 (概念 {min(half,len(concepts))} / 三级 {min(half,len(l3))})')

    init()
    per_board: list[float] = []
    key_present = {f: 0 for f in FIELDS}
    val_present = {f: 0 for f in FIELDS}
    rows = []
    t_all = time.time()
    try:
        for b in sample:
            t0 = time.time()
            mi = safe_call(tq.get_more_info, stock_code=b['code'], field_list=[]) or {}
            sn = safe_call(tq.get_market_snapshot, stock_code=b['code'], field_list=[]) or {}
            dt = time.time() - t0
            per_board.append(dt)
            row = {'code': b['code'], 'name': b['name'], 'sec_s': round(dt, 3)}
            for f in FIELDS:
                v = mi.get(f, sn.get(f))
                if f in mi or f in sn:
                    key_present[f] += 1
                if _is_present(v):
                    val_present[f] += 1
                row[f] = v
            rows.append(row)
    finally:
        close()
    total = time.time() - t_all

    n = len(per_board)
    avg = sum(per_board) / n if n else 0
    srt = sorted(per_board)
    p95 = srt[int(0.95 * (n - 1))] if n else 0
    print(f'\n=== 计时 (双调用/板块, n={n}) ===')
    print(f'  均值 {avg*1000:.0f}ms  P95 {p95*1000:.0f}ms  总 {total:.1f}s')
    print(f'  外推 {len(all_boards)} 板块: 均值 {avg*len(all_boards):.1f}s  '
          f'P95 {p95*len(all_boards):.1f}s  {"OK<30s" if avg*len(all_boards)<30 else "超30s预算!"}')

    print(f'\n=== 字段可用性 (n={n}) ===')
    print(f'  {"field":<10} {"key在":>6} {"值非空":>6}')
    for f in FIELDS:
        print(f'  {f:<10} {key_present[f]:>3}/{n:<2}  {val_present[f]:>3}/{n:<2}')

    print('\n=== 样例 (首2块) ===')
    for r in rows[:2]:
        print(f'  [{r["code"]}] {r["name"]}  {r["sec_s"]}s')
        for f in FIELDS:
            print(f'      {f} = {r.get(f)}')


if __name__ == '__main__':
    main()
