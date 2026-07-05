"""e2e_legacy.py: 旧版端到端家族引导

用法:
  python e2e_legacy.py mock       # 跑 mock 数据 e2e
  python e2e_legacy.py real       # 跑真实 tqcenter 数据 e2e
  python e2e_legacy.py live_5min  # 跑 5 分钟连续采集
"""
import sys
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEP = _HERE / '_deprecated' / 'e2e_legacy'

TARGETS = {
    'mock': DEP / 'mock.py',
    'real': DEP / 'real.py',
    'live_5min': DEP / 'live_5min.py',
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        print('用法: python e2e_legacy.py {mock|real|live_5min}')
        print()
        for k, v in TARGETS.items():
            print(f'  {k:<12} → {v.relative_to(_HERE)}')
        sys.exit(1)

    target = TARGETS[sys.argv[1]]
    # 透传剩余参数
    rc = subprocess.call([sys.executable, str(target), *sys.argv[2:]])
    sys.exit(rc)


if __name__ == '__main__':
    main()