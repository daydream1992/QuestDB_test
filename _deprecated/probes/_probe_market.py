"""测 get_market_snapshot 能否不限 100 只"""
import sys, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')
sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq

tq.initialize(os.path.abspath(__file__))
try:
    # 测不同方式调用
    print('--- 测 1: 不传 code, 看是否全市场 ---')
    try:
        result = tq.get_market_snapshot([], None)
        if result is not None:
            print(f'  返回 {len(result)} 条')
            if len(result) > 0:
                print(f'  首行 keys: {list(result.iloc[0].index) if hasattr(result, "iloc") else list(result[0].keys())}')
        else:
            print('  None')
    except Exception as e:
        print(f'  异常: {e}')

    print('--- 测 2: 传 200 个 code, 看是否真能突破 100 ---')
    codes = [f'000{i:03d}.SZ' for i in range(1, 201)]
    try:
        result = tq.get_market_snapshot(codes, None)
        if result is not None:
            print(f'  返回 {len(result)} 条')
    except Exception as e:
        print(f'  异常: {e}')

    print('--- 测 3: 看 price_df 数据格式 ---')
    print('  price_df attr:', tq.price_df if hasattr(tq, 'price_df') else 'N/A')
finally:
    tq.close()
