"""检查板块 Inside/Outside 涨跌停家数字段"""
import sys
sys.path.insert(0, '.')
from lib.qdb import connect, query_df

con = connect()

# 查询板块的 Inside/Outside (实际是涨跌停家数)
df = query_df(con, """
SELECT code, snapshot_time, UpHome, DownHome, Inside, Outside, ZAFPre3
FROM qd_sector_snapshot
WHERE snapshot_time > dateadd('m', -10, now())
  AND code_type = 'sector'
ORDER BY snapshot_time DESC
LIMIT 20
""")

print("=" * 70)
print("qd_sector_snapshot 板块数据 (Inside=跌停家数, Outside=涨停家数)")
print("=" * 70)
print(f"{'板块代码':12s} {'涨':>4s} {'跌':>4s} {'涨停':>4s} {'跌停':>4s} {'涨幅%':>6s}")
for i, r in df.iterrows():
    code = r['code']
    up = r['UpHome'] or 0
    dn = r['DownHome'] or 0
    inside = r['Inside'] or 0  # 跌停家数
    outside = r['Outside'] or 0  # 涨停家数
    zaf = r['ZAFPre3'] or 0
    print(f"  {code:10s} 涨{up:3d} 跌{dn:3d} 涨停{outside:3d} 跌停{inside:3d} 涨幅{zaf:6.2f}%")

# 检查 Inside/Outside 是否真的有数据
df_count = query_df(con, """
SELECT
    COUNT(*) as total,
    COUNT(Inside) as has_inside,
    COUNT(Outside) as has_outside
FROM qd_sector_snapshot
WHERE snapshot_time > dateadd('m', -10, now())
  AND code_type = 'sector'
""")
print(f"\n字段填充率:")
print(f"  总行数: {df_count.iloc[0]['total']}")
print(f"  Inside 填充: {df_count.iloc[0]['has_inside']}")
print(f"  Outside 填充: {df_count.iloc[0]['has_outside']}")

# 看一下指数层 (qd_index_snapshot)
print(f"\n" + "=" * 70)
print("qd_index_snapshot 大盘指数数据")
print("=" * 70)
df_idx = query_df(con, """
SELECT code, snapshot_time, UpHome, DownHome, ZAFPre3, Now
FROM qd_index_snapshot
WHERE snapshot_time > dateadd('m', -10, now())
ORDER BY snapshot_time DESC
LIMIT 10
""")
for i, r in df_idx.iterrows():
    code = r['code']
    up = r['UpHome'] or 0
    dn = r['DownHome'] or 0
    zaf = r['ZAFPre3'] or 0
    now = r['Now'] or 0
    print(f"  {code:12s} 涨{up:3d} 跌{dn:3d} 涨幅{zaf:6.2f}% 价{now:.2f}")

con.close()