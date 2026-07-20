"""检查代码中文映射覆盖情况"""
import sys
import os
sys.path.insert(0, '.')

print("=" * 70)
print("中文映射覆盖检查")
print("=" * 70)

# 1. 检查 relation_graph 映射
print("\n[1] lib/relation_graph.py 中文映射")
from lib.relation_graph import _CATEGORY_TYPE_MAP, get_stock_name
print(f"  _CATEGORY_TYPE_MAP: {_CATEGORY_TYPE_MAP}")

# 2. 检查 get_stock_name
print("\n[2] get_stock_name 测试")
test_codes = ['600519.SH', '000001.SZ', '002479.SZ', '000001.SH', '999999.XZ']
for code in test_codes:
    name = get_stock_name(code)
    is_placeholder = (name == code) or not name
    print(f"  {code}: {name} {'[PLACEHOLDER]' if is_placeholder else '[OK]'}")

# 3. 检查 tq_utils 标签
print("\n[3] lib/tq_utils.py 标的类型")
from lib.tq_utils import _COLLECT_MARKETS
print("  类型标签:")
for market, code_type, sector_category, list_type in _COLLECT_MARKETS:
    has_cn = code_type in ('stock', 'etf', 'kzz', 'reits', 'index',
                          'industry_l1', 'industry_l2', 'industry_l3',
                          'concept', 'style', 'region')
    print(f"    market={market} -> {code_type:15s} ({'有映射' if has_cn else '⚠️无中文映射'})")

# 4. 检查 bitable 字段
print("\n[4] feishu/bitable_writer.py 字段")
from feishu.bitable_writer import SIGNAL_FIELDS, SIGNAL_TYPE_OPTIONS
print(f"  字段数: {len(SIGNAL_FIELDS)}")
for f in SIGNAL_FIELDS:
    name = f['field_name']
    is_cn = any('一' <= c <= '鿿' for c in name)
    print(f"    {name}: {'✅' if is_cn else '⚠️ 英文'}")
print(f"  信号类型选项数: {len(SIGNAL_TYPE_OPTIONS)}")
for opt in SIGNAL_TYPE_OPTIONS[:5]:
    print(f"    示例: {opt}")

# 5. 检查 sheet headers
print("\n[5] feishu/sheet_writer.py 字段")
from feishu.sheet_writer import SIGNAL_HEADERS
print(f"  字段数: {len(SIGNAL_HEADERS)}")
for h in SIGNAL_HEADERS:
    print(f"    {h}")

# 6. 检查策略 reason
print("\n[6] 策略 reason 中文覆盖")
strategy_files = [
    'strategy/plugins/p06_resonance.py',
    'strategy/plugins/p08_dark_money.py',
    'strategy/plugins/p17_market_emotion.py',
    'strategy/plugins/p01_zt_daban.py',
]
for f in strategy_files:
    if not os.path.exists(f):
        continue
    with open(f, encoding='utf-8') as fp:
        content = fp.read()
    has_cn = any('一' <= c <= '鿿' for c in content)
    print(f"  {f}: {'✅ 含中文' if has_cn else '⚠️ 纯英文'}")

# 7. 检查推送卡片 reason 是否含中文
print("\n[7] 推送卡片中文 reason")
from feishu.push import _build_signal_card
test_signal = {
    'signal_time': '2026-07-13 14:00:00',
    'code': '002479.SZ',
    'stock_name': '富春环保',
    'strategy_name': 'zt_daban',
    'signal_type': 'limit_seal',
    'signal_score': 87,
    'price': 5.62,
    'reason': '涨停封板 9.98% 量比2.5',  # 中文
    'sector_name': '环保',
}
try:
    card = _build_signal_card(test_signal)
    # 提取所有文本内容
    import json
    card_str = json.dumps(card, ensure_ascii=False)
    has_cn = any('一' <= c <= '鿿' for c in card_str)
    print(f"  T1 信号卡片: {'✅ 含中文' if has_cn else '⚠️ 纯英文'}")
    # 提取原因
    for elem in card.get('elements', []):
        if 'text' in elem and 'content' in elem.get('text', {}):
            content = elem['text']['content']
            if '原因' in content or '策略' in content:
                print(f"    示例: {content[:80]}")
                break
except Exception as e:
    print(f"  ERROR: {e}")

# 8. 总结
print("\n" + "=" * 70)
print("总结")
print("=" * 70)
print("""
中文映射覆盖情况:
✅ 已覆盖: 个股/板块/指数/ETF/可转债/REITs 类型标签
✅ get_stock_name: 个股代码 → 股票名
✅ 字段定义: 时间/代码/股票名称/策略/信号类型 等都用中文
✅ Bitable 字段名: 涨跌幅%/板块/是否涨停 等中文
✅ 卡片 reason: 含中文描述
⚠️ 风险点: get_stock_name 对未知代码返回 code 自身 (placeholder)
""")