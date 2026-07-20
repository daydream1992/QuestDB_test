"""验证飞书推送频控逻辑"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from lib.qdb import connect, query_df
from datetime import datetime, timedelta

con = connect()

print("=" * 60)
print("验证 2: 频控逻辑")
print("=" * 60)

# 检查 qd_signal_log 频控记录
print("\n检查 qd_signal_log 频控记录:")

# 最近1小时的推送记录
df = query_df(con, """
SELECT
    log_time,
    strategy_name,
    pushed,
    cooldown_sec
FROM qd_signal_log
WHERE log_time > dateadd('h', -1, now())
ORDER BY log_time DESC
LIMIT 20
""")

print(f"最近1小时内记录数: {len(df)}")

# 分析推送频率
if len(df) > 0:
    df['log_time'] = pd.to_datetime(df['log_time'])

    # 按分钟分组统计
    df['minute'] = df['log_time'].dt.strftime('%Y-%m-%d %H:%M')
    minute_counts = df.groupby('minute').size()

    print("\n每分钟推送次数:")
    for minute, count in minute_counts.head(10).items():
        print(f"  {minute}: {count}条")

    # 检查是否超过 2条/分钟
    exceeded = minute_counts[minute_counts > 2]
    if len(exceeded) > 0:
        print(f"\n⚠️ 超过2条/分钟的时段: {len(exceeded)}个")
        for minute, count in exceeded.items():
            print(f"  {minute}: {count}条")
    else:
        print("\n✅ 无超过2条/分钟的时段")

# 检查重复推送（同code同action）
print("\n检查重复推送:")
df_dup = query_df(con, """
SELECT
    strategy_name,
    COUNT(*) as cnt
FROM qd_signal_log
WHERE log_time > dateadd('hour', -1, now())
GROUP BY strategy_name
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 10
""")

print(f"重复推送的key数: {len(df_dup)}")
if len(df_dup) > 0:
    print("\n重复推送最多的key:")
    for _, row in df_dup.iterrows():
        print(f"  {row['strategy_name']}: {row['cnt']}次")

# 检查冷却时间配置
print("\n" + "=" * 60)
print("验证 3: 冷却时间配置")
print("=" * 60)

try:
    from feishu.config import SIGNAL_COOLDOWN_SEC
    print(f"SIGNAL_COOLDOWN_SEC: {SIGNAL_COOLDOWN_SEC}")
except:
    print("⚠️ SIGNAL_COOLDOWN_SEC 未定义")

try:
    from feishu.config import DECISION_COOLDOWN_SEC
    print(f"DECISION_COOLDOWN_SEC: {DECISION_COOLDOWN_SEC}")
except:
    print("⚠️ DECISION_COOLDOWN_SEC 未定义")

# 检查代码中的硬编码冷却时间
print("\n检查硬编码冷却时间:")
with open('feishu/push.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'total_seconds() < 60' in content:
        print("❌ 发现硬编码 60秒冷却时间")
    if 'total_seconds() < 300' in content:
        print("❌ 发现硬编码 300秒冷却时间")
    if 'SIGNAL_COOLDOWN_SEC' in content:
        print("✅ 使用配置项 SIGNAL_COOLDOWN_SEC")

con.close()

print("\n" + "=" * 60)
print("验证总结")
print("=" * 60)
print("""
1. 字段映射: ❌ 无metadata时板块字段为空
2. 频控逻辑: 待分析
3. 冷却时间: 待检查
""")
