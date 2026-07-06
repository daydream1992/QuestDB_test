"""验证中文名映射是否正确生效

验证方式:
  1. 直接测试: lib/relation_graph.get_stock_name() 能否正确返回中文名
  2. 输出: PASS / FAIL 列表

用法:
  python scripts/verify_stock_name.py
"""

import sys
import os
import re

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

def test_get_stock_name():
    """测试 1: 直接查名称映射"""
    from lib.relation_graph import load_from_json, DEFAULT_JSON_DIR, get_stock_name, _name_data

    load_from_json(DEFAULT_JSON_DIR)

    if not _name_data:
        print('FAIL: _name_data 为空, 名称映射.json 可能未加载')
        return False

    sample_code = next(iter(_name_data.keys()))
    expected = _name_data[sample_code].get('name', '')
    actual = get_stock_name(sample_code)
    if actual != expected:
        print(f'FAIL: get_stock_name("{sample_code}") 返回 "{actual}", 期望 "{expected}"')
        return False
    print(f'PASS: get_stock_name("{sample_code}") = "{actual}"')

    known = {'002747.SZ': '埃斯顿', '002008.SZ': '大族激光', '002580.SZ': '圣阳股份'}
    all_ok = True
    for code, expected_name in known.items():
        actual_name = get_stock_name(code)
        if actual_name != expected_name:
            print(f'FAIL: get_stock_name("{code}") = "{actual_name}", 期望 "{expected_name}"')
            all_ok = False
        else:
            print(f'PASS: get_stock_name("{code}") = "{actual_name}"')
    return all_ok


def test_registry_initialized():
    """测试 2: process_registry 初始化"""
    from runner.process_registry import initialize, get_all_processes

    initialize()
    procs = get_all_processes()
    expected_tags = ['auction_monitor', 'intraday_loop', 'subscribe', 'overseer',
                     'daily_init', 'daily_close', 'daily_summary', 'verify_tables']
    missing = [t for t in expected_tags if t not in procs]
    if missing:
        print(f'FAIL: 注册表缺 {missing}')
        return False
    for tag in expected_tags:
        p = procs[tag]
        print(f'PASS: 注册表 [{tag}] ({p.reg_name}) type={p.proc_type}')
    return True


if __name__ == '__main__':
    results = []

    print('=== 测试 1: get_stock_name 中文名映射 ===')
    r1 = test_get_stock_name()
    results.append(('get_stock_name', r1))
    print()

    print('=== 测试 2: process_registry 注册表 ===')
    r2 = test_registry_initialized()
    results.append(('registry_init', r2))
    print()

    print('=== 汇总 ===')
    all_pass = True
    for name, result in results:
        status = 'PASS' if result else 'FAIL'
        if not result:
            all_pass = False
        print(f'  [{status}] {name}')

    sys.exit(0 if all_pass else 1)
