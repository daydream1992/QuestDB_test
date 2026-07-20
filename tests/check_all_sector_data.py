"""检查所有可用板块数据源"""
import sys
sys.path.insert(0, '.')
from lib.qdb import connect, query_df

con = connect()

# qd_sector_snapshot 字段
print("=" * 60)
print("1. qd_sector_snapshot 字段 (c2 已写入)")
print("=" * 60)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_sector_snapshot' ORDER BY ordinal_position
""")
for _, r in df.iterrows():
    print(f"  {r['column_name']}")

# qd_stock_intraday 字段 (c3 写入, 板块相关)
print("\n" + "=" * 60)
print("2. qd_stock_intraday 字段 (c3 写入)")
print("=" * 60)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_stock_intraday' ORDER BY ordinal_position
""")
for _, r in df.iterrows():
    print(f"  {r['column_name']}")

# qd_sector_flow 字段
print("\n" + "=" * 60)
print("3. qd_sector_flow 字段 (sector_flow 写入)")
print("=" * 60)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_sector_flow' ORDER BY ordinal_position
""")
for _, r in df.iterrows():
    print(f"  {r['column_name']}")

# qd_stock_snapshot Zjl/Amount 等
print("\n" + "=" * 60)
print("4. qd_stock_snapshot 关键字段 (c2 写入, 个股)")
print("=" * 60)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_stock_snapshot'
ORDER BY ordinal_position
""")
print(f"  字段数: {len(df)}")
print("  关键字段: code, snapshot_time, Now, LastClose, Volume, Amount, Zjl")

# qd_stock_daily 字段 (板块涨跌停)
print("\n" + "=" * 60)
print("5. qd_stock_daily 字段 (板块涨跌停关键)")
print("=" * 60)
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_stock_daily' ORDER BY ordinal_position
""")
print(f"  字段数: {len(df)}")
print("  关键字段: code, date, Now, LastClose, ZTGPNum, fLianB")

con.close()