"""验证飞书推送字段映射完整性"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.qdb import connect, query_df
from feishu.bitable_writer import _signal_to_record

con = connect()

print("=" * 60)
print("验证 1: qd_decisions 表数据")
print("=" * 60)

df = query_df(con, """
SELECT code, strategy_name, action, price, reason
FROM qd_decisions
ORDER BY decision_time DESC
LIMIT 5
""")

print(f"总行数: {len(df)}")
print("\n样本数据:")
for idx, row in df.iterrows():
    print(f"\n[{idx}] {row['code']} | {row['strategy_name']}")
    print(f"  action: {row['action']}")
    print(f"  price: {row['price']}")
    print(f"  reason: {row['reason'][:100] if row['reason'] else 'EMPTY'}...")

print("\n" + "=" * 60)
print("验证 2: _signal_to_record 字段映射")
print("=" * 60)

# 模拟一个完整的 signal
test_signal = {
    'signal_time': '09:35:00',
    'code': '002479.SZ',
    'stock_name': '富春环保',
    'strategy_name': 'dark_money',
    'signal_type': 'buy',
    'signal_score': 87,
    'price': 5.62,
    'volume': 1000000,
    'reason': '大单流入 涨停封板 9.98%',
    'metadata': {
        'change_pct': 9.98,
        'sectors': ['环保', '节能'],
        'is_zt': True,
    }
}

record = _signal_to_record(test_signal)
print("\n完整 metadata 时的映射结果:")
for k, v in record.items():
    print(f"  {k}: {v}")

# 测试无 metadata 的情况
test_signal_no_meta = {
    'signal_time': '09:35:00',
    'code': '002479.SZ',
    'stock_name': '富春环保',
    'strategy_name': 'zt_daban',
    'signal_type': 'limit_seal',
    'signal_score': 87,
    'price': 5.62,
    'volume': 1000000,
    'reason': '涨停封板 9.98%',
    'sector_name': '环保',
}

record_no_meta = _signal_to_record(test_signal_no_meta)
print("\n无 metadata 时的映射结果:")
for k, v in record_no_meta.items():
    print(f"  {k}: {v}")

print("\n" + "=" * 60)
print("验证 3: 检查 Bitable 字段缺失情况")
print("=" * 60)

missing_fields = []
expected_fields = ['涨跌幅%', '板块', '是否涨停']
for field in expected_fields:
    if field not in record_no_meta or record_no_meta[field] is None:
        missing_fields.append(field)

if missing_fields:
    print(f"缺失字段: {missing_fields}")
else:
    print("所有字段都有值")

con.close()
