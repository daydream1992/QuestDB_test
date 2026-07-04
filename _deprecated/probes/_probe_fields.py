"""探查 tqcenter.get_more_info 实际返回什么字段"""
import sys, os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')
sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq

tq.initialize(os.path.abspath(__file__))
try:
    for code in ['000001.SZ', '600519.SH', '300750.SZ']:
        tdx = code if '.' in code else code + ('.SH' if code.startswith('6') else '.SZ')
        data = tq.get_more_info(tdx, field_list=[])
        print(f'\n=== {tdx} (返回 {len(data) if data else 0} 字段) ===')
        if data:
            # 只打印跟"价/成交/资金"相关的 key
            keys_of_interest = [k for k in data.keys() if any(x in k.lower() for x in ['now', 'last', 'price', 'close', 'open', 'high', 'low', 'vol', 'amo', 'amount', 'nowvol', 'zaf', 'buyp', 'sellp'])]
            for k in keys_of_interest:
                print(f'  {k}: {data[k]}')
finally:
    tq.close()
