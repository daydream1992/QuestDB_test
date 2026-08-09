"""testv10.2 个股钻取计时+字段探测 (funnel step 5 实测)

目的: 回答两个 go/no-go:
  - 计时: more_info+snapshot 双调用/股 耗时 → 外推 ~1500 股 (HOT/NEW 板块成分股去重) 是否撞 60s 轮预算
  - 字段: 个股级 FCAmo/fLianB/fHSL/ZAF/Zjl/HisHigh/LastZTHzNum 等钻取字段是否非空 (板级已验, 股级补验)
依赖: lib/tq_client, mapping_store (取真实成分股样本)
跑法: python testv10.2/_probe_stock_drill.py [--n 80]
注: 盘前数据为昨日值; 定结构+计时下界, 盘中会慢 (2-3x), 按 v10.1 实测 ~15ms/single more_info 推算盘中。
"""
import argparse
import time

import bootstrap
bootstrap.ensure_paths()

from lib.tq_client import safe_call, init, close  # noqa: E402
from tqcenter import tq  # noqa: E402
import mapping_store  # noqa: E402
import settings as cfg  # noqa: E402

STOCK_FIELDS = ['ZAF', 'FCAmo', 'fLianB', 'fHSL', 'Zjl', 'FzAmo',
                'HisHigh', 'LastZTHzNum', 'ShapeValue', 'BetaValue']


def _present(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v == '':
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--n', type=int, default=80, help='抽样个股数')
    args = parser.parse_args()

    ms = mapping_store.MappingStore(cfg.MAPPING_PARQUET)
    # 取若干 monitor 板块的成分股去重作样本 (模拟 funnel step 4 的钻取目标)
    boards = ms.boards(cfg.MONITOR_LEVELS)[:5]
    codes: set[str] = set()
    for b in boards:
        codes |= ms.stocks_of(b['code'])
    sample = list(codes)[:args.n]
    print(f'样本: {len(sample)} 股 (取自 {len(boards)} monitor 板块成分股去重)')

    init()
    per_stock: list[float] = []
    key_present = {f: 0 for f in STOCK_FIELDS}
    val_present = {f: 0 for f in STOCK_FIELDS}
    samples = []
    t_all = time.time()
    try:
        for code in sample:
            t0 = time.time()
            mi = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}
            sn = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[]) or {}
            dt = time.time() - t0
            per_stock.append(dt)
            row = {'code': code, 'sec': round(dt, 3)}
            for f in STOCK_FIELDS:
                v = mi.get(f, sn.get(f))
                if f in mi or f in sn:
                    key_present[f] += 1
                if _present(v):
                    val_present[f] += 1
                row[f] = v
            samples.append(row)
    finally:
        close()
    total = time.time() - t_all

    n = len(per_stock)
    avg = sum(per_stock) / n if n else 0
    srt = sorted(per_stock)
    p95 = srt[int(0.95 * (n - 1))] if n else 0
    print(f'\n=== 计时 (双调用/股, n={n}) ===')
    print(f'  均值 {avg*1000:.1f}ms  P95 {p95*1000:.1f}ms  总 {total:.1f}s')
    for univ in (500, 1000, 1500):
        print(f'  外推 {univ} 股: 均值 {avg*univ:.1f}s  P95 {p95*univ:.1f}s')

    print(f'\n=== 个股字段可用性 (n={n}) ===')
    for f in STOCK_FIELDS:
        print(f'  {f:<12} key {key_present[f]:>3}/{n}  值 {val_present[f]:>3}/{n}')

    print('\n=== 样例 (首2只) ===')
    for r in samples[:2]:
        print(f'  [{r["code"]}] {ms.stock_name(r["code"])}  {r["sec"]}s')
        for f in STOCK_FIELDS:
            print(f'      {f} = {r.get(f)}')


if __name__ == '__main__':
    main()
