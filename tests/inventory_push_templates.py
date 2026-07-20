"""盘点飞书推送模板 - 在终端打印确认"""
import sys
import os
sys.path.insert(0, '.')

print("=" * 70)
print("飞书推送模板盘点")
print("=" * 70)

# 1. push.py 中的推送函数
print("\n[1] feishu/push.py 推送入口函数")
print("-" * 70)
push_funcs = [
    ('push_text', '纯文本推送', '无卡片'),
    ('push_signal', 'T1 信号卡片推送', '单标信号, 含频控'),
    ('push_decision', 'T1 决策卡片推送', '单标决策, 含单标频控'),
    ('push_focus_pool', 'T3 Focus池表格卡片', '50只重点股表格'),
    ('push_decision_aggregated', '决策入桶聚合', '桶满flush T2 聚合卡片'),
]
for i, (name, desc, feature) in enumerate(push_funcs, 1):
    print(f"  {i}. {name}")
    print(f"     用途: {desc}")
    print(f"     特点: {feature}")
    print()

# 2. 卡片构造器
print("[2] feishu/push.py 卡片构造器")
print("-" * 70)
card_builders = [
    ('_build_signal_card', 'T1 信号卡片', '蓝/绿/红/橙 按 signal_type 着色'),
    ('_build_decision_card', 'T1 决策卡片', '蓝/绿/红/橙 按 action 着色'),
    ('_build_aggregate_card', 'T2 聚合卡片', '蓝色, 头部+列表+跳转'),
    ('build_review_card', 'T4 收盘复盘卡片', '绿松色, 4分区+k4扩展'),
    ('_build_focus_pool_card', 'T3 Focus池卡片', '蓝色, 表格形式'),
]
for i, (name, desc, feature) in enumerate(card_builders, 1):
    print(f"  {i}. {name}")
    print(f"     卡片类型: {desc}")
    print(f"     样式: {feature}")
    print()

# 3. feishu/__init__.py 导出
print("[3] feishu/__init__.py 公共 API")
print("-" * 70)
import feishu
public_apis = [
    ('create_doc', '创建飞书文档'),
    ('append_to_doc', '追加到文档'),
    ('append_signal', '追加信号到文档'),
    ('create_daily_report', '创建日报文档'),
    ('get_doc_url', '获取文档URL'),
    ('append_rows', '追加行到表格'),
    ('write_signal_batch', '批量写信号到表格'),
    ('auto_daily_sheet', '自动创建/切换子表格'),
    ('ensure_headers', '确保表头'),
    ('get_sheet_url', '获取表格URL'),
    ('SIGNAL_HEADERS', '表格表头常量'),
    ('create_bitable', '创建多维表格'),
    ('append_records', '追加记录到多维表格'),
    ('write_signal_batch_bitable', '批量写信号到多维表格'),
    ('auto_daily_table', '自动创建/切换数据表'),
    ('get_bitable_url', '获取多维表格URL'),
    ('log_signals', '三通道一键写入 (push+sheet+bitable)'),
    ('get_tenant_token', '获取飞书token'),
]
for i, (name, desc) in enumerate(public_apis, 1):
    print(f"  {i:2d}. {name:30s} - {desc}")

# 4. 调用方
print("\n[4] 调用方 (谁在用)")
print("-" * 70)
callers = [
    ('runner/intraday_loop.py', '_process_decisions', '写决策 + 飞书聚合'),
    ('runner/auction_monitor.py', '_process_decisions', '竞价决策 + push'),
    ('runner/intraday_loop.py', 'push_focus_pool', '每60轮推送 focus 池'),
    ('strategy/intraday_engine.py', 'log_signals', '异动信号写表'),
]
for caller, func, desc in callers:
    print(f"  - {caller}")
    print(f"      函数: {func}")
    print(f"      作用: {desc}")
    print()

# 5. 颜色映射
print("[5] 颜色映射 (_COLOR_MAP)")
print("-" * 70)
from feishu.push import _COLOR_MAP
type_cn = {
    'buy': '买入', 'sell': '卖出',
    'stop_profit': '止盈', 'stop_loss': '止损',
    'surge_up': '急涨', 'surge_down': '急跌',
    'limit_seal': '涨停封板', 'limit_break': '炸板',
    'capital_in': '资金流入', 'capital_out': '资金流出',
    'warn': '警告', 'observe': '观察', 'hold': '持有',
}
color_cn = {'green': '绿', 'red': '红', 'orange': '橙', 'blue': '蓝', 'grey': '灰'}
for stype, color in _COLOR_MAP.items():
    cn = type_cn.get(stype, stype)
    color_zh = color_cn.get(color, color)
    print(f"  {stype:15s} ({cn:8s}) -> {color:8s} ({color_zh})")

# 6. 总结
print("\n" + "=" * 70)
print("推送模板总结")
print("=" * 70)
print(f"""
模板数量统计:
  - 推送入口函数: {len(push_funcs)} 个
  - 卡片构造器:   {len(card_builders)} 个
  - 公共 API:     {len(public_apis)} 个
  - 颜色映射:     {len(_COLOR_MAP)} 种

卡片类型:
  T1 单标的精推 (信号/决策) - column_set 分栏 + 进度条
  T2 5分钟桶聚合 - 头部+列表+多维表格跳转
  T3 Focus 池表格 - 50只重点股快照
  T4 收盘复盘 - 4分区+k4扩展 (情绪/数据/策略/异常)

覆盖场景:
  - 实时信号 (10s 异动)
  - 盘中决策 (60s 策略产出)
  - 竞价监控 (3-5s 推送)
  - 焦点池 (每60轮推送)
  - 收盘复盘 (1次/日)
""")