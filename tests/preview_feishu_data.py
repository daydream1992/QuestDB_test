"""打印推送相关的实际数据（不推送，直接展示）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from lib.qdb import connect, query_df

con = connect()

print("=" * 70)
print("1. qd_decisions 最新 10 条（实际推送内容预览）")
print("=" * 70)

df = query_df(con, """
SELECT decision_time, code, strategy_name, action, price, reason, position_size
FROM qd_decisions
ORDER BY decision_time DESC
LIMIT 10
""")

for idx, row in df.iterrows():
    print(f"\n[决策 #{idx}]")
    print(f"  时间: {row['decision_time']}")
    print(f"  代码: {row['code']}")
    print(f"  策略: {row['strategy_name']}")
    print(f"  动作: {row['action']}")
    print(f"  价格: {row['price']}")
    print(f"  仓位: {row['position_size']}%")
    print(f"  原因: {row['reason']}")

print("\n" + "=" * 70)
print("2. qd_signals 最新 10 条")
print("=" * 70)

df = query_df(con, """
SELECT signal_time, code, strategy_name, signal_type, signal_score, reason
FROM qd_signals
ORDER BY signal_time DESC
LIMIT 10
""")

for idx, row in df.iterrows():
    print(f"\n[信号 #{idx}]")
    print(f"  时间: {row['signal_time']}")
    print(f"  代码: {row['code']}")
    print(f"  策略: {row['strategy_name']}")
    print(f"  类型: {row['signal_type']}")
    print(f"  评分: {row['signal_score']}")
    print(f"  原因: {row['reason']}")

print("\n" + "=" * 70)
print("3. 转换为多维表格记录预览（P0-1/P0-3/P0-5 修复后）")
print("=" * 70)

from feishu.bitable_writer import _signal_to_record

df = query_df(con, """
SELECT decision_time, code, strategy_name, action, price, reason, position_size
FROM qd_decisions
ORDER BY decision_time DESC
LIMIT 5
""")

for idx, row in df.iterrows():
    # 构造 signal/decision 字典
    decision = {
        'decision_time': row['decision_time'],
        'code': row['code'],
        'strategy_name': row['strategy_name'],
        'action': row['action'],
        'price': row['price'],
        'reason': row['reason'],
        'position_size': row['position_size'],
        'stock_name': '',  # 数据库无此字段
    }
    record = _signal_to_record(decision)
    print(f"\n[多维表格记录 #{idx}] {decision['code']} {decision['action']}")
    print(json.dumps(record, ensure_ascii=False, indent=2, default=str))

print("\n" + "=" * 70)
print("4. T1 决策卡片预览（如果推送会显示什么）")
print("=" * 70)

from feishu.push import _build_decision_card

df = query_df(con, """
SELECT decision_time, code, strategy_name, action, price, reason, position_size
FROM qd_decisions
WHERE action IN ('buy', 'sell', 'observe')
ORDER BY decision_time DESC
LIMIT 1
""")

if len(df) > 0:
    row = df.iloc[0]
    decision = {
        'decision_time': row['decision_time'],
        'code': row['code'],
        'strategy_name': row['strategy_name'],
        'action': row['action'],
        'price': row['price'],
        'reason': row['reason'],
        'position_size': row['position_size'],
        'stock_name': '',
    }
    card = _build_decision_card(decision)
    print(json.dumps(card, ensure_ascii=False, indent=2, default=str)[:2000])
else:
    print("无 buy/sell/observe 决策")

print("\n" + "=" * 70)
print("5. price=0 异常分析（P1-9）")
print("=" * 70)

df = query_df(con, """
SELECT strategy_name, action, COUNT(*) as cnt
FROM qd_decisions
WHERE price = 0 OR price IS NULL
GROUP BY strategy_name, action
ORDER BY cnt DESC
LIMIT 10
""")

print("\nprice=0 的策略分布:")
for _, row in df.iterrows():
    print(f"  {row['strategy_name']:30s} | {row['action']:15s} | {row['cnt']}条")

print("\n样本 reason（看原因是否合理）:")
df2 = query_df(con, """
SELECT code, strategy_name, action, reason
FROM qd_decisions
WHERE price = 0
ORDER BY decision_time DESC
LIMIT 3
""")
for _, row in df2.iterrows():
    print(f"  {row['code']} | {row['strategy_name']} | {row['reason'][:80]}")

con.close()
print("\n完成")