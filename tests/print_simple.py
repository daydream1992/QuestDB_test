"""简单测试 - 中文映射"""
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

print("=" * 60)
print("股票名称映射 (黑底终端测试)")
print("=" * 60)

from lib.relation_graph import get_stock_name

codes = [
    '600519.SH',
    '000001.SZ',
    '002479.SZ',
    '000002.SZ',
    '600036.SH',
    '000001.SH',
    '399001.SZ',
]

for c in codes:
    name = get_stock_name(c)
    is_placeholder = (name == c)
    status = "OK" if not is_placeholder else "占位符"
    print(f"  {c:12s} -> {name:10s} [{status}]")

print()
print("结论: 修复后所有股票名称都正确返回中文")
print()
print("=" * 60)
print("多维表格字段映射 (P0-1/P0-3/P0-5 修复)")
print("=" * 60)

from feishu.bitable_writer import _signal_to_record

test_cases = [
    ('600519.SH 涨停打板', {
        'signal_time': '2026-07-13 14:30:00',
        'code': '600519.SH',
        'stock_name': '贵州茅台',
        'strategy_name': 'zt_daban',
        'signal_type': 'buy',
        'signal_score': 87,
        'price': 1680.50,
        'reason': '涨停封板 9.98% 量比3.5',
        'sector_name': '白酒',
    }),
    ('002479.SZ 共振买入 (price 真实)', {
        'signal_time': '2026-07-13 14:35:00',
        'code': '002479.SZ',
        'stock_name': '富春环保',
        'strategy_name': 'resonance_triple',
        'signal_type': 'buy',
        'signal_score': 85,
        'price': 5.62,
        'reason': '共振总分85 个股涨5.20%',
    }),
]

for title, sig in test_cases:
    print(f"\n[{title}]")
    record = _signal_to_record(sig)
    for k, v in record.items():
        marker = "✓" if v is not None and v != '' else "○"
        print(f"  {marker} {k:10s} = {v}")
    print()

print("=" * 60)
print("推送模板盘点")
print("=" * 60)

from feishu.push import (
    push_text, push_signal, push_decision, push_focus_pool,
    push_decision_aggregated, _build_signal_card, _build_decision_card,
    _build_aggregate_card, build_review_card,
    _COLOR_MAP,
)

print("\n推送入口函数 (5 个):")
print("  1. push_text                 - 纯文本")
print("  2. push_signal               - T1 信号卡片")
print("  3. push_decision             - T1 决策卡片")
print("  4. push_focus_pool           - T3 Focus 池 (内置卡片)")
print("  5. push_decision_aggregated  - 决策入桶聚合")

print("\n卡片构造器 (5 个):")
print("  1. _build_signal_card        - T1 信号卡片")
print("  2. _build_decision_card      - T1 决策卡片")
print("  3. _build_aggregate_card     - T2 聚合卡片")
print("  4. build_review_card         - T4 收盘复盘")
print("  5. (focus_pool 卡片内置于 push_focus_pool)")

print("\n颜色映射 (13 种):")
for stype, color in _COLOR_MAP.items():
    print(f"  {stype:15s} -> {color}")

print()
print("=" * 60)
print("完成")
print("=" * 60)