"""验证飞书推送问题（简化版）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.qdb import connect, query_df

con = connect()

print("=" * 60)
print("飞书推送验证报告")
print("=" * 60)

# 验证1: 字段映射
print("\n[1] 字段映射验证")
from feishu.bitable_writer import _signal_to_record

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

record = _signal_to_record(test_signal_no_meta)

# 检查关键字段
issues = []
if record.get('板块') is None:
    issues.append('板块字段为空（P0-1确认）')
if record.get('涨跌幅%') is None:
    issues.append('涨跌幅%为空')
if record.get('是否涨停') is None:
    issues.append('是否涨停为空')

if issues:
    print("  问题:")
    for i in issues:
        print(f"    - {i}")
else:
    print("  OK: 所有关键字段有值")

# 验证2: 频控
print("\n[2] 频控验证")
df = query_df(con, """
SELECT COUNT(*) as cnt
FROM qd_signal_log
WHERE log_time > dateadd('h', -1, now())
""")
count = df.iloc[0]['cnt']
print(f"  最近1小时推送: {count}条")
if count <= 2:
    print("  OK: 频控正常（<=2条/小时）")
else:
    print(f"  WARNING: 推送量可能过多")

# 验证3: 推送数据量
print("\n[3] 数据量验证")
df_dec = query_df(con, "SELECT COUNT(*) as cnt FROM qd_decisions")
df_sig = query_df(con, "SELECT COUNT(*) as cnt FROM qd_signals")

dec_cnt = df_dec.iloc[0]['cnt']
sig_cnt = df_sig.iloc[0]['cnt']

print(f"  qd_decisions: {dec_cnt}条")
print(f"  qd_signals: {sig_cnt}条")

if dec_cnt == 0 and sig_cnt == 0:
    print("  WARNING: 无推送数据（策略可能未产出）")

# 验证4: price字段
print("\n[4] price字段验证")
df = query_df(con, """
SELECT price, COUNT(*) as cnt
FROM qd_decisions
WHERE price = 0 OR price IS NULL
GROUP BY price
LIMIT 5
""")
if len(df) > 0:
    print("  WARNING: price=0的记录:")
    for _, row in df.iterrows():
        print(f"    price={row['price']}: {row['cnt']}条")

# 验证5: 配置检查
print("\n[5] 配置检查")
try:
    from feishu.config import DRY_RUN
    print(f"  DRY_RUN={DRY_RUN}")
    if DRY_RUN:
        print("  WARNING: Dry-run模式开启，推送被拦截")
except:
    print("  DRY_RUN配置未找到")

con.close()

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
