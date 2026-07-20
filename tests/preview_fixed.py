"""模拟修复后新数据写入的效果"""
import sys
sys.path.insert(0, '.')
import json
from lib.relation_graph import get_stock_name
from feishu.bitable_writer import _signal_to_record

print("=" * 70)
print("修复后效果演示（模拟新写入数据）")
print("=" * 70)

# 模拟 p17 market_emotion 修复后的输出
print("\n[1] p17_market_emotion 修复后 (reason 前缀 [市场级])")
print("-" * 70)
decision_market = {
    'decision_time': '2026-07-13 14:03:01',
    'code': '000001.SH',
    'stock_name': get_stock_name('000001.SH'),
    'strategy_name': 'market_emotion',
    'action': 'watch',
    'price': 0,
    'position_size': 0,
    'reason': '[市场级] 大盘情绪偏空(低迷/调整) 涨停0 涨跌比0.17 建议空仓观望',
    'score': 25.0,
}
record = _signal_to_record(decision_market)
print(json.dumps(record, ensure_ascii=False, indent=2, default=str))

# 模拟 p06 resonance 修复后（真实价格）
print("\n[2] p06_resonance 修复后 (price 填真实值)")
print("-" * 70)
decision_resonance = {
    'decision_time': '2026-07-13 14:03:01',
    'code': '600396.SH',
    'stock_name': get_stock_name('600396.SH'),
    'strategy_name': 'resonance_triple',
    'action': 'buy',
    'price': 13.44,  # 修复前为 0, 修复后为真实价格
    'position_size': 10,
    'reason': '共振总分85 个股涨5.20%',
    'score': 85.0,
}
record = _signal_to_record(decision_resonance)
print(json.dumps(record, ensure_ascii=False, indent=2, default=str))

# 模拟 p08 dark_money 修复后（topN 限制）
print("\n[3] p08_dark_money 修复后 (topN=20 限制)")
print("-" * 70)
print("  修复前: 130 条 watch 刷屏")
print("  修复后: ≤20 条 watch (按 score 降序)")
print("  阈值: CANCEL_DIFF_MIN=50, WTB_MIN=10")

# 模拟 Bitable 记录（P0-1 板块降级）
print("\n[4] Bitable 记录 (P0-1 板块字段降级提取)")
print("-" * 70)
signal_with_sectors = {
    'signal_time': '2026-07-13 14:03:01',
    'code': '002479.SZ',
    'stock_name': get_stock_name('002479.SZ'),
    'strategy_name': 'zt_daban',
    'signal_type': 'limit_seal',
    'signal_score': 87,
    'price': 5.62,
    'volume': 1000000,
    'reason': '涨停封板 9.98%',
    'sector_name': '环保',  # 修复: 从 sector_name 降级提取
}
record = _signal_to_record(signal_with_sectors)
print(json.dumps(record, ensure_ascii=False, indent=2, default=str))

print("\n" + "=" * 70)
print("修复总结对比表")
print("=" * 70)

comparison = """
| 项目             | 修复前               | 修复后                    |
|-----------------|---------------------|--------------------------|
| 板块字段         | 永远 null            | 4级降级提取                |
| 类型校验         | 静默失败             | 8个测试用例通过            |
| Sheet/Bitable 字段 | 5个字段不一致       | 9个核心字段对齐            |
| 时间解析失败     | 静默回退             | logger.warning            |
| 聚合卡片去重     | 无                   | seen_codes 逻辑            |
| p08 推送量       | 130条/轮刷屏         | ≤20条/轮 (topN)            |
| p06 buy 价格     | 全部为0              | 真实价格                   |
| p17 reason 标记  | 与个股混淆           | [市场级] 前缀              |
| DB stock_name    | 为空                 | 回填名称                   |
| dark_money 联动  | 模块禁用策略在跑     | enabled=false             |
"""

print(comparison)