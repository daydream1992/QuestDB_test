"""验证 get_stock_name lazy-load 修复"""
import sys
sys.path.insert(0, '.')

# 场景 1: 不预先加载, 直接调用
print("=" * 70)
print("场景 1: 未预先加载 -> 应自动 lazy-load")
print("=" * 70)

# 注意: 这里是新进程, _name_data 一定为空
from lib.relation_graph import get_stock_name, _name_data

print(f"调用前 _name_data keys: {len(_name_data)}")

test_codes = ['600519.SH', '002479.SZ', '000001.SZ', '000001.SH', '000002.SZ', '999999.XZ']
for code in test_codes:
    name = get_stock_name(code)
    is_placeholder = (name == code)
    status = "[PLACEHOLDER]" if is_placeholder else "[OK]"
    print(f"  {code} -> {name} {status}")

print(f"\n调用后 _name_data keys: {len(_name_data)}")
print(f"lazy-load 触发: {'是' if len(_name_data) > 0 else '否'}")