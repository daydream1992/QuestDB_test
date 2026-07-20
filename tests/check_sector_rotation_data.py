"""板块轮动数据源验证 - 打印到终端"""
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from lib.qdb import connect, query_df

con = connect()

print("=" * 70)
print("板块轮动数据源验证")
print("=" * 70)

# 1. qd_sector_snapshot 实际数据
print("\n【1】qd_sector_snapshot 板块数据 (30分钟内)")
print("-" * 70)

df = query_df(con, """
SELECT code, code_type, UpHome, DownHome, Inside, Outside, ZAFPre3, Now, snapshot_time
FROM qd_sector_snapshot
WHERE snapshot_time > dateadd('m', -30, now())
  AND code_type IN ('sector', 'style', 'industry', 'concept')
ORDER BY ZAFPre3 DESC
LIMIT 20
""")

print(f"{'板块代码':12s} {'类型':8s} {'上涨':>4s} {'下跌':>4s} {'涨停':>4s} {'跌停':>4s} {'涨幅%':>7s} {'当前价':>8s}")
print("-" * 70)

for i, r in df.iterrows():
    code = r['code']
    ct = r['code_type']
    up = r['UpHome'] or 0
    dn = r['DownHome'] or 0
    inside = r['Inside'] or 0  # 跌停家数
    outside = r['Outside'] or 0  # 涨停家数
    zaf = r['ZAFPre3'] or 0
    now = r['Now'] or 0
    print(f"  {code:10s} {ct:8s} 涨{up:3d} 跌{dn:3d} 涨停{outside:3d} 跌停{inside:3d} {zaf:7.2f}% {now:8.2f}")

# 2. qd_sector_flow 资金流数据
print("\n【2】qd_sector_flow 资金流数据 (最近)")
print("-" * 70)

df_flow = query_df(con, """
SELECT code, flow_time, main_net, big_net, total_flow, net_pct
FROM qd_sector_flow
ORDER BY flow_time DESC
LIMIT 15
""")

if len(df_flow) > 0:
    print(f"{'板块代码':12s} {'资金流时间':25s} {'主力净流入':>12s} {'大单净流入':>12s} {'总净流入':>10s} {'净占比%':>7s}")
    print("-" * 70)
    for _, r in df_flow.iterrows():
        print(f"  {r['code']:10s} {str(r['flow_time']):25s} {r['main_net'] or 0:12.0f} {r['big_net'] or 0:12.0f} {r['total_flow'] or 0:10.0f} {r['net_pct'] or 0:7.2f}%")
else:
    print("  (暂无数据)")

# 3. qd_index_snapshot 大盘指数
print("\n【3】qd_index_snapshot 大盘指数 (无 UpHome/DownHome)")
print("-" * 70)

df_idx = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_index_snapshot'
ORDER BY ordinal_position
""")

print("  字段列表:")
for _, r in df_idx.iterrows():
    print(f"    - {r['column_name']}")

# 查询实际指数数据
df_real_idx = query_df(con, """
SELECT code, Now, ZAFPre3, Volume, Amount, snapshot_time
FROM qd_index_snapshot
WHERE snapshot_time > dateadd('m', -30, now())
ORDER BY snapshot_time DESC
LIMIT 10
""")

print(f"\n  实际数据 (无涨跌家数字段):")
for _, r in df_real_idx.iterrows():
    print(f"    {r['code']:12s} 价={r['Now']:8.2f} 涨幅={r['ZAFPre3'] or 0:6.2f}% 时间={r['snapshot_time']}")

# 4. 大盘背离数据来源检查
print("\n【4】大盘背离数据来源")
print("-" * 70)
print("  qd_index_snapshot 无 UpHome/DownHome，无法直接计算大盘背离")
print("  需要通过 qd_stock_intraday 聚合个股数据计算:")
print("    - 上涨家数 = COUNT(code WHERE ZAF > 0)")
print("    - 下跌家数 = COUNT(code WHERE ZAF < 0)")
print("    - 涨停家数 = SUM(FCAmo > 0)  from 涨停池")
print("    - 跌停家数 = SUM(FCAmo < 0)  from 跌停池")

# 检查 qd_stock_intraday 近期数据
df_stocks = query_df(con, """
SELECT COUNT(*) as cnt,
       SUM(CASE WHEN ZAF > 0 THEN 1 ELSE 0 END) as up_cnt,
       SUM(CASE WHEN ZAF < 0 THEN 1 ELSE 0 END) as down_cnt,
       SUM(CASE WHEN FCAmo > 0 THEN 1 ELSE 0 END) as zt_cnt,
       SUM(CASE WHEN FCAmo < 0 THEN 1 ELSE 0 END) as dt_cnt
FROM qd_stock_intraday
WHERE snapshot_time > dateadd('m', -10, now())
  AND code_type = 'stock'
""")

if len(df_stocks) > 0:
    r = df_stocks.iloc[0]
    print(f"\n  qd_stock_intraday 近期统计 (10分钟内):")
    print(f"    总股票数: {r['cnt']}")
    print(f"    上涨家数: {r['up_cnt'] or 0}")
    print(f"    下跌家数: {r['down_cnt'] or 0}")
    print(f"    涨停家数: {r['zt_cnt'] or 0}")
    print(f"    跌停家数: {r['dt_cnt'] or 0}")

# 5. 数据源汇总
print("\n【5】板块轮动功能数据源汇总")
print("-" * 70)
print("  ✓ 板块涨幅:     qd_sector_snapshot.ZAFPre3")
print("  ✓ 上涨家数:     qd_sector_snapshot.UpHome")
print("  ✓ 下跌家数:     qd_sector_snapshot.DownHome")
print("  ✓ 涨停家数:     qd_sector_snapshot.Outside")
print("  ✓ 跌停家数:     qd_sector_snapshot.Inside")
print("  ✓ 板块资金流:   qd_sector_flow.main_net / total_flow")
print("  ? 大盘背离:     需从 qd_stock_intraday 聚合计算")
print("  ? 涨跌停统计:   qd_stock_intraday (已验证可用)")

print("\n" + "=" * 70)
print("验证完成")
print("=" * 70)

con.close()
