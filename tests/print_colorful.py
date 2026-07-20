"""黑底终端打印 - 用 ANSI 颜色让输出更清晰"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

# ANSI 颜色代码 (黑底白字友好配色)
class C:
    H = '\033[1;36m'   # 高亮青色 (标题)
    Y = '\033[1;33m'   # 黄色 (警告/重要)
    G = '\033[1;32m'   # 绿色 (OK/买入)
    R = '\033[1;31m'   # 红色 (错误/卖出)
    B = '\033[1;34m'   # 蓝色 (信息)
    M = '\033[1;35m'   # 紫色 (分类)
    W = '\033[1;37m'   # 白色 (主文本)
    D = '\033[0;37m'   # 暗灰 (次要)
    N = '\033[0m'      # 重置

print(f"{C.H}{'='*70}")
print(f"{C.H}飞书推送系统 - 数据预览 (黑底终端友好版)")
print(f"{C.H}{'='*70}{C.N}")

from lib.qdb import connect, query_df
con = connect()

# 1. qd_decisions 实际数据
print(f"\n{C.Y}【1】qd_decisions 实际数据 (10 条){C.N}")
print(f"{C.D}{'-'*70}{C.N}")

df = query_df(con, """
SELECT decision_time, code, strategy_name, action, price, reason, stock_name
FROM qd_decisions
ORDER BY decision_time DESC
LIMIT 10
""")

for i, r in df.iterrows():
    # action 着色
    action_color = {
        'buy': C.G, 'sell': C.R, 'warn': C.Y,
        'observe': C.B, 'hold': C.D, 'watch': C.M,
    }.get(r['action'], C.W)

    print(f"\n  {C.D}[{i}]{C.N} {C.W}{r['code']}{C.N} | "
          f"{C.M}{r['strategy_name']}{C.N} | "
          f"{action_color}{r['action']}{C.N}")
    print(f"      {C.D}时间:   {C.W}{r['decision_time']}{C.N}")
    print(f"      {C.D}股票名: {C.W}{r['stock_name'] or '(空)'}{C.N}")
    print(f"      {C.D}价格:   {C.W}{r['price']}{C.N}")
    print(f"      {C.D}原因:   {C.W}{r['reason']}{C.N}")

# 2. qd_signals
print(f"\n{C.Y}【2】qd_signals 实际数据 (10 条){C.N}")
print(f"{C.D}{'-'*70}{C.N}")

df = query_df(con, """
SELECT signal_time, code, strategy_name, signal_type, signal_score, reason
FROM qd_signals
ORDER BY signal_time DESC
LIMIT 10
""")

for i, r in df.iterrows():
    stype_color = {
        'death_cross': C.R, 'golden_cross': C.G,
        'break_support': C.R, 'break_resistance': C.G,
    }.get(r['signal_type'], C.W)
    print(f"\n  {C.D}[{i}]{C.N} {C.W}{r['code']}{C.N} | "
          f"{C.M}{r['strategy_name']}{C.N} | "
          f"{stype_color}{r['signal_type']}{C.N}")
    print(f"      {C.D}时间:   {C.W}{r['signal_time']}{C.N}")
    print(f"      {C.D}评分:   {C.Y}{r['signal_score']}{C.N}")
    print(f"      {C.D}原因:   {C.W}{r['reason']}{C.N}")

# 3. 多维表格预览
print(f"\n{C.Y}【3】多维表格记录预览 (修复后){C.N}")
print(f"{C.D}{'-'*70}{C.N}")

from feishu.bitable_writer import _signal_to_record
from lib.relation_graph import get_stock_name
import json

print(f"\n  {C.G}[案例 A] 600519.SH 涨停打板买入{C.N}")
print(f"  {C.D}股票: 贵州茅台 (上证主板){C.N}")
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
print(f"  {C.M}字段映射:{C.N}")
for k, v in record.items():
    color = C.G if v and v is not None and v != '' else C.D
    print(f"    {C.B}{k:10s}{C.N} = {color}{v!r}{C.N}")

print(f"\n  {C.G}[案例 B] 002479.SZ 共振买入 (修复后 price 真实){C.N}")
decision = {
    'decision_time': '2026-07-13 14:35:00',
    'code': '002479.SZ',
    'stock_name': get_stock_name('002479.SZ'),
    'strategy_name': 'resonance_triple',
    'action': 'buy',
    'price': 5.62,
    'position_size': 10,
    'reason': '共振总分85 个股涨5.20%',
}
record = _signal_to_record(decision)
print(f"  {C.M}字段映射:{C.N}")
for k, v in record.items():
    color = C.G if v and v is not None and v != '' else C.D
    print(f"    {C.B}{k:10s}{C.N} = {color}{v!r}{C.N}")

# 4. T1 决策卡片
print(f"\n{C.Y}【4】T1 决策卡片 (飞书实际推送格式){C.N}")
print(f"{C.D}{'-'*70}{C.N}")

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
print(f"  {C.G}┌─ Header ─────────────────────{C.N}")
print(f"  {C.G}│  标题: {C.W}{card['header']['title']['content']}{C.N}")
print(f"  {C.G}│  颜色: {C.Y}{card['header']['template']}{C.N}")
print(f"  {C.G}├─ 内容 ───────────────────────{C.N}")
for elem in card['elements']:
    if 'columns' in elem:
        print(f"  {C.B}│  [分栏]{C.N}")
        for col in elem['columns']:
            for e in col.get('elements', []):
                if 'text' in e:
                    content = e['text']['content']
                    print(f"  {C.B}│{C.N}    {C.W}{content}{C.N}")
    elif 'text' in elem:
        content = elem['text']['content']
        print(f"  {C.B}│{C.N}    {C.W}{content}{C.N}")
    elif elem.get('tag') == 'hr':
        print(f"  {C.D}│  ────────────{C.N}")
print(f"  {C.G}└──────────────────────────────{C.N}")

# 5. T2 聚合卡片
print(f"\n{C.Y}【5】T2 聚合卡片 (5分钟桶聚合){C.N}")
print(f"{C.D}{'-'*70}{C.N}")

from feishu.push import _build_aggregate_card
mock_top = [
    {'code': '002479.SZ', 'stock_name': '富春环保', 'price': 5.62,
     'action': 'buy', 'score': 87, 'strategy': 'dark_money', 'reason': '大单流入'},
    {'code': '600519.SH', 'stock_name': '贵州茅台', 'price': 1680.50,
     'action': 'sell', 'score': 45, 'strategy': 'alpha_breakout', 'reason': '破位'},
]
card = _build_aggregate_card(mock_top, total=12)
print(f"  {C.B}┌─ Header ─────────────────────{C.N}")
print(f"  {C.B}│  标题: {C.W}{card['header']['title']['content']}{C.N}")
print(f"  {C.B}│  颜色: {C.Y}{card['header']['template']}{C.N}")
print(f"  {C.B}├─ 列表 ───────────────────────{C.N}")
for elem in card['elements']:
    if 'text' in elem:
        content = elem['text']['content']
        # 🟢/🔴 着色
        if '🟢' in content:
            content = content.replace('🟢', f'{C.G}🟢{C.W}')
        if '🔴' in content:
            content = content.replace('🔴', f'{C.R}🔴{C.W}')
        print(f"  {C.B}│{C.N}  {content[:80]}")
print(f"  {C.B}└──────────────────────────────{C.N}")

# 6. 数据统计
print(f"\n{C.Y}【6】数据统计{C.N}")
print(f"{C.D}{'-'*70}{C.N}")

df = query_df(con, "SELECT COUNT(*) as cnt FROM qd_decisions")
print(f"  {C.W}qd_decisions 总数: {C.Y}{df.iloc[0]['cnt']}{C.N}")
df = query_df(con, "SELECT COUNT(*) as cnt FROM qd_signals")
print(f"  {C.W}qd_signals 总数:  {C.Y}{df.iloc[0]['cnt']}{C.N}")

print(f"\n  {C.M}qd_decisions 策略分布:{C.N}")
df = query_df(con, """
SELECT strategy_name, action, COUNT(*) as cnt
FROM qd_decisions
GROUP BY strategy_name, action
ORDER BY cnt DESC
LIMIT 10
""")
for _, r in df.iterrows():
    action_c = {
        'buy': C.G, 'sell': C.R, 'warn': C.Y,
        'watch': C.M, 'hold': C.D, 'observe': C.B,
    }.get(r['action'], C.W)
    print(f"    {C.W}{r['strategy_name']:30s}{C.N} | "
          f"{action_c}{r['action']:10s}{C.N} | {C.Y}{r['cnt']}条{C.N}")

# 7. 总结
print(f"\n{C.H}{'='*70}")
print(f"{C.G}完成 - 终端确认所有内容{C.N}")
print(f"{C.H}{'='*70}{C.N}")

con.close()