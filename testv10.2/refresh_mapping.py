"""v10.2 板块-个股映射 正向构建 (盘后刷新)
抄 market_data_export.py 的正向取数: 对每个板块调 get_stock_list_in_sector 拿成分股,
不反推。输出长表 Parquet: 板块代码,板块名称,行业级别,个股代码,个股名称。

行业级别: 一级(16) / 二级(17) / 三级(18) / 概念(12)
"""
import sys
import os
import time
sys.path.insert(0, r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq
import pandas as pd

tq.initialize(__file__)
t0 = time.time()
try:
    LEVELS = [('16', '一级'), ('17', '二级'), ('18', '三级'), ('12', '概念')]
    rows = []
    for market, lv_cn in LEVELS:
        boards = tq.get_stock_list(market, list_type=1) or []
        print(f'{lv_cn}(market={market}): {len(boards)} 个板块')
        for i, b in enumerate(boards):
            bc = b.get('Code', '')
            bn = b.get('Name', '')
            if not bc:
                continue
            stocks = tq.get_stock_list_in_sector(bc, list_type=1) or []
            for s in stocks:
                rows.append({'板块代码': bc, '板块名称': bn, '行业级别': lv_cn,
                             '个股代码': s.get('Code', ''), '个股名称': s.get('Name', '')})
            if (i + 1) % 100 == 0:
                print(f'  进度 {i+1}/{len(boards)}, 累计 {len(rows)} 行')
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sector_mapping.parquet')
    df.to_parquet(out, index=False, compression='snappy')
    print(f'\nrows={len(df)}  独立板块={df["板块代码"].nunique()}  独立个股={df["个股代码"].nunique()}')
    print('每级别板块数:', df.drop_duplicates(subset=['板块代码'])['行业级别'].value_counts().to_dict())
    print('每级别关系数:', df['行业级别'].value_counts().to_dict())
    print(f'-> {out} ({os.path.getsize(out)//1024} KB), 耗时 {time.time()-t0:.1f}s')
finally:
    tq.close()
