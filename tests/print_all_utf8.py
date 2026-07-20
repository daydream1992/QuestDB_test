"""用 UTF-8 输出所有需要确认的内容"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')  # 关键: 强制 UTF-8 输出

print("=" * 70)
print("【1】qd_decisions 实际数据 (10 条)")
print("=" * 70)

from lib.qdb import connect, query_df
con = connect()
df = query_df(con, """
SELECT decision_time, code, strategy_name, action, price, reason, stock_name
FROM qd_decisions
ORDER BY decision_time DESC
LIMIT 10
""")

for i, r in df.iterrows():
    print(f"\n  [{i}] {r['code']} | {r['strategy_name']} | {r['action']}")
    print(f"      时间:   {r['decision_time']}")
    print(f"      股票名: {r['stock_name'] or '(空)'}")
    print(f"      价格:   {r['price']}")
    print(f"      原因:   {r['reason']}")

print("\n" + "=" * 70)
print("【2】qd_signals 实际数据 (10 条)")
print("=" * 70)

df = query_df(con, """
SELECT signal_time, code, strategy_name, signal_type, signal_score, reason
FROM qd_signals
ORDER BY signal_time DESC
LIMIT 10
""")

for i, r in df.iterrows():
    print(f"\n  [{i}] {r['code']} | {r['strategy_name']} | {r['signal_type']}")
    print(f"      时间:   {r['signal_time']}")
    print(f"      评分:   {r['signal_score']}")
    print(f"      原因:   {r['reason']}")

print("\n" + "=" * 70)
print("【3】多维表格记录预览 (修复后的字段映射)")
print("=" * 70)

from feishu.bitable_writer import _signal_to_record
from lib.relation_graph import get_stock_name

# 模拟一个完整的决策
decision = {
    'decision_time': '2026-07-13 14:30:00',
    'code': '600519.SH',
    'stock_name': get_stock_name('600519.SH'),
    'strategy_name': 'zt_daban',
    'action': 'buy',
    'price': 1680.50,
    'position_size': 10,
    'reason': '涨停封板 9.98% 量比3.5',
    'sector_name': '白酒',
}
record = _signal_to_record(decision)
import json
print(f"\n  代码: {decision['code']} ({decision['stock_name']})")
print(f"  多维表格记录:")
print(json.dumps(record, ensure_ascii=False, indent=4, default=str))

# 模拟共振决策 (修复后有真实价格)
print("\n  [共振买入案例]")
decision = {
    'decision_time': '2026-07-13 14:35:00',
    'code': '002479.SZ',
    'stock_name': get_stock_name('002479.SZ'),
    'strategy_name': 'resonance_triple',
    'action': 'buy',
    'price': 5.62,  # 修复: 真实价格
    'position_size': 10,
    'reason': '共振总分85 个股涨5.20%',
}
record = _signal_to_record(decision)
print(f"  多维表格记录:")
print(json.dumps(record, ensure_ascii=False, indent=4, default=str))

print("\n" + "=" * 70)
print("【4】T1 决策卡片 (实际推送的飞书卡片结构)")
print("=" * 70)

from feishu.push import _build_decision_card
decision = {
    'decision_time': '2026-07-13 14:30:00',
    'code': '600519.SH',
    'stock_name': '贵州茅台',
    'strategy_name': 'zt_daban',
    'action': 'buy',
    'price': 1680.50,
    'reason': '涨停封板 9.98% 量比3.5',
    'position_size': 10,
    'score': 90,
}
card = _build_decision_card(decision)
print("\n  Header:")
print(f"    标题: {card['header']['title']['content']}")
print(f"    颜色: {card['header']['template']}")
print("\n  卡片内容:")
for elem in card['elements']:
    if 'columns' in elem:
        print("    [分栏]")
        for col in elem['columns']:
            for e in col.get('elements', []):
                if 'text' in e:
                    content = e['text']['content']
                    # 解码 lark_md 的 markdown
                    print(f"      • {content}")
    elif 'text' in elem:
        content = elem['text']['content']
        print(f"    • {content}")
    elif 'tag' in elem and elem['tag'] == 'hr':
        print("    ────────────────")

print("\n" + "=" * 70)
print("【5】T2 聚合卡片预览")
print("=" * 70)

from feishu.push import _build_aggregate_card
# 模拟一个聚合场景
mock_top = [
    {'code': '002479.SZ', 'stock_name': '富春环保', 'price': 5.62,
     'action': 'buy', 'score': 87, 'strategy': 'dark_money', 'reason': '大单流入'},
    {'code': '600519.SH', 'stock_name': '贵州茅台', 'price': 1680.50,
     'action': 'sell', 'score': 45, 'strategy': 'alpha_breakout', 'reason': '破位'},
]
card = _build_aggregate_card(mock_top, total=12)
print(f"\n  Header: {card['header']['title']['content']}")
print(f"  颜色: {card['header']['template']}")
for elem in card['elements']:
    if 'text' in elem:
        print(f"  • {elem['text']['content'][:100]}")

print("\n" + "=" * 70)
print("【6】数据统计")
print("=" * 70)
df = query_df(con, "SELECT COUNT(*) as cnt FROM qd_decisions")
print(f"  qd_decisions 总数: {df.iloc[0]['cnt']}")
df = query_df(con, "SELECT COUNT(*) as cnt FROM qd_signals")
print(f"  qd_signals 总数:  {df.iloc[0]['cnt']}")

# 按策略分组统计
df = query_df(con, """
SELECT strategy_name, action, COUNT(*) as cnt
FROM qd_decisions
GROUP BY strategy_name, action
ORDER BY cnt DESC
LIMIT 10
""")
print("\n  qd_decisions 策略分布 (前10):")
for _, r in df.iterrows():
    print(f"    {r['strategy_name']:30s} | {r['action']:10s} | {r['cnt']}条")

con.close()

print("\n" + "=" * 70)
print("完成")
print("=" * 70)