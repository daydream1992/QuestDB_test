"""大盘情绪数据依赖检查"""
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from lib.qdb import connect, query_df

con = connect()

print("=" * 70)
print("大盘情绪 (k3_sentiment.py) 数据依赖分析")
print("=" * 70)

# k3_sentiment.py 依赖的数据
print("\n【1】ctx.pricevol_df (全场涨跌) → qd_pricevol")
print("-" * 70)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_pricevol'
ORDER BY ordinal_position
""")
print(f"  qd_pricevol 字段数: {len(df)}")
print("  核心字段: code, Now, LastClose, snapshot_time")

# 检查是否有数据
df_pv = query_df(con, """
SELECT COUNT(*) as cnt, MAX(snapshot_time) as last_ts
FROM qd_pricevol
WHERE snapshot_time > dateadd('m', -30, now())
""")
if len(df_pv) > 0:
    r = df_pv.iloc[0]
    print(f"  近期数据: {r['cnt']} 条, 最新时间: {r['last_ts']}")
else:
    print("  近期数据: 无")

print("\n【2】ctx.snapshot_focus_df (涨停/封单/连板) → qd_stock_snapshot")
print("-" * 70)
df_ss = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_stock_snapshot'
ORDER BY ordinal_position
""")
print(f"  qd_stock_snapshot 字段数: {len(df_ss)}")
print("  核心字段: code, Now, LastClose, FCAmo, ZTPrice, FCb, fLianB, Zjl_HB, Max")

# 检查涨停相关数据
df_zt = query_df(con, """
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN FCAmo > 0 THEN 1 ELSE 0 END) as zt_cnt,
    SUM(CASE WHEN FCAmo < 0 THEN 1 ELSE 0 END) as dt_cnt,
    SUM(CASE WHEN FCAmo = 0 AND Max >= ZTPrice * 0.999 THEN 1 ELSE 0 END) as break_cnt
FROM qd_stock_snapshot
WHERE snapshot_time > dateadd('m', -30, now())
  AND code_type = 'stock'
""")
if len(df_zt) > 0:
    r = df_zt.iloc[0]
    print(f"  近期统计: 总={r['total']} 涨停={r['zt_cnt']} 跌停={r['dt_cnt']} 炸板={r['break_cnt']}")

print("\n【3】ctx.index_snapshot (主指数) → qd_index_snapshot")
print("-" * 70)
df_idx = query_df(con, """
SELECT code, Now, LastClose, ZAFPre3, snapshot_time
FROM qd_index_snapshot
WHERE snapshot_time > dateadd('m', -30, now())
  AND code IN ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')
ORDER BY snapshot_time DESC
LIMIT 10
""")
print("  四大指数 (000001.SH/399001.SZ/399006.SZ/000688.SH):")
for _, r in df_idx.iterrows():
    zaf = r['ZAFPre3'] or 0
    print(f"    {r['code']}: 价={r['Now']} 涨幅={zaf:.2f}%")

print("\n【4】relation_graph (板块映射) → data/market_data/")
print("-" * 70)
from lib.relation_graph import get_stock_sectors, load_from_json

# 检查板块映射数据
test_codes = ['600519.SH', '000001.SZ', '002479.SZ']
print("  板块映射测试:")
for code in test_codes:
    sectors = get_stock_sectors(code)
    sector_names = [s.get('block_name', 'N/A') for s in (sectors or [])]
    print(f"    {code}: {sector_names}")

print("\n【5】qd_sentiment_snapshot_min (情绪快照写入目标)")
print("-" * 70)
df_sent = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_sentiment_snapshot_min'
ORDER BY ordinal_position
""")
print(f"  qd_sentiment_snapshot_min 字段:")
for _, r in df_sent.iterrows():
    print(f"    - {r['column_name']}")

# 检查历史数据
df_sent_data = query_df(con, """
SELECT COUNT(*) as cnt, MIN(snapshot_time) as first_ts, MAX(snapshot_time) as last_ts
FROM qd_sentiment_snapshot_min
""")
if len(df_sent_data) > 0:
    r = df_sent_data.iloc[0]
    print(f"  历史数据: {r['cnt']} 条, {r['first_ts']} ~ {r['last_ts']}")

print("\n【6】qd_sentiment_event_log (变盘事件写入目标)")
print("-" * 70)
df_evt = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_sentiment_event_log'
ORDER BY ordinal_position
""")
print(f"  qd_sentiment_event_log 字段:")
for _, r in df_evt.iterrows():
    print(f"    - {r['column_name']}")

# 检查变盘事件
df_evt_data = query_df(con, """
SELECT COUNT(*) as cnt, event_type, MAX(event_time) as last_ts
FROM qd_sentiment_event_log
GROUP BY event_type
""")
print(f"  变盘事件统计:")
for _, r in df_evt_data.iterrows():
    print(f"    {r['event_type']}: {r['cnt']} 条, 最新: {r['last_ts']}")

print("\n【7】k3_sentiment.run() 数据依赖汇总")
print("=" * 70)
print("""
  输入数据:
    ✓ ctx.pricevol_df    → qd_pricevol        (全场涨跌家数)
    ✓ ctx.snapshot_focus_df → qd_stock_snapshot (涨停/封单/连板)
    ✓ ctx.index_snapshot  → qd_index_snapshot  (四大指数)
    ✓ relation_graph      → data/market_data/  (板块映射)

  计算逻辑:
    1. _calc_market_breadth(pricevol_df)
       → 涨跌家数(up_cnt, down_cnt) + 涨跌比(udr)
    2. _calc_seal_stats(snapshot_focus_df)
       → 涨停/跌停/炸板数 + 封板率 + 最高连板
    3. build_sector_strength(snapshot_focus_df)
       → 板块强度排行 (需板块映射)
    4. _main_index_zaf(index_snapshot)
       → 四大指数涨幅
    5. detect_divergence(index_zaf, udr, up_cnt, down_cnt)
       → 背离检测 (价宽背离 + 指数分化)

  输出表:
    ✓ qd_sentiment_snapshot_min  (分钟情绪快照)
    ✓ qd_sentiment_event_log      (变盘事件)
    ✓ push_text()                 (飞书推送)

  关键依赖:
    ✓ FCAmo  (封单额) → 判断涨停/跌停/炸板
    ✓ Zjl_HB (主力净流入) → 板块强度计算
    ✓ fLianB (连板数) → 连板统计
    ✓ relation_graph → 个股→板块映射
""")

print("=" * 70)
print("分析完成")
print("=" * 70)

con.close()
