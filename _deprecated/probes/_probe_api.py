"""探查 tq 全部接口"""
import sys, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')
sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq
print('TQ 接口列表:')
for name in dir(tq):
    if not name.startswith('_'):
        print(f'  - {name}')
