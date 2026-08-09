"""testv10.2 sys.path 引导 (DRY)

脚本路径: K:/QuestDB_test/testv10.2/bootstrap.py
用途: 目录名带点 (testv10.2) 不可作包, 所有入口/模块统一调 ensure_paths(),
      把 _PROJ_ROOT (喂 lib/ + config/) 与 _THIS_DIR (喂兄弟 settings/mapping_store)
      双插入 sys.path (幂等)。
依赖: 无 (纯 sys.path 操作)
克隆自: testv10.1/main.py:24-29 + testv10.1/ticker.py:21-23,34-36
"""

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(_THIS_DIR)


def ensure_paths() -> None:
    """把项目根 + 本目录插入 sys.path (幂等)。

    用法: 入口脚本 ``import bootstrap; bootstrap.ensure_paths()`` 之后再 import 兄弟
    模块 (settings/mapping_store) 与 lib/。被 import 的模块也可调它自保。
    """
    if _PROJ_ROOT not in sys.path:
        sys.path.insert(0, _PROJ_ROOT)
    if _THIS_DIR not in sys.path:
        sys.path.insert(0, _THIS_DIR)
    # Windows GBK 控制台遇 emoji/中文会 UnicodeEncodeError 或乱码; 统一 stdout/stderr 转 utf-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:  # noqa: BLE001  (非 TextIOWrapper 时跳过)
            pass
