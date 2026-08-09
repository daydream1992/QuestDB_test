"""testv10.2 个股快照 盘后刷新 (自包含)
对每只个股调 get_more_info + get_market_snapshot, 合并写 stock_snapshot.parquet。
英文 API 字段名原样保留 (FCAmo/ZAF/...); 5 档盘口 list 拍平成逗号串。
依赖: 先跑 refresh_mapping.py (从 sector_mapping.parquet 取个股列表+名称)。"""
import sys
import os
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
MAPPING = os.path.join(THIS, 'sector_mapping.parquet')
OUT = os.path.join(THIS, 'stock_snapshot.parquet')
MAX_WORKERS = 4


def _flat(v):
    if isinstance(v, list):
        return ','.join(str(x) for x in v)
    return v


def fetch(code, name):
    row = {'个股代码': code, '中文名称': name}
    try:
        for k, v in (tq.get_more_info(code, field_list=[]) or {}).items():
            row[k] = _flat(v)
    except Exception as e:
        row['_more_info_err'] = str(e)
    try:
        for k, v in (tq.get_market_snapshot(code, field_list=[]) or {}).items():
            if k not in row:
                row[k] = _flat(v)
    except Exception as e:
        row['_snapshot_err'] = str(e)
    return row


def main():
    tq.initialize(__file__)
    try:
        mp = pd.read_parquet(MAPPING)
        name_map = dict(zip(mp['个股代码'], mp['个股名称']))
        codes = list(name_map.keys())
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f'个股: {len(codes)} 只, 查询时间: {now_str}')
        t0 = time.time()
        rows = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = [ex.submit(fetch, c, name_map[c]) for c in codes]
            for i, fut in enumerate(as_completed(futs), 1):
                rows.append(fut.result())
                if i % 500 == 0 or i == len(codes):
                    print(f'  进度 {i}/{len(codes)}, {time.time()-t0:.1f}s')
        df = pd.DataFrame(rows)
        df['查询时间'] = now_str
        df.to_parquet(OUT, index=False, compression='snappy')
        err = df.filter(regex='_err$').notna().sum().to_dict() if any('_err' in c for c in df.columns) else {}
        print(f'\nrows={len(df)} cols={len(df.columns)} -> {OUT}')
        print(f'大小: {os.path.getsize(OUT)/1024:.0f} KB, 耗时 {time.time()-t0:.1f}s, 错误: {err}')
    finally:
        tq.close()


if __name__ == '__main__':
    main()
