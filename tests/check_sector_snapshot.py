"""检查 qd_sector_snapshot 数据"""
import sys
sys.path.insert(0, '.')
from lib.qdb import connect, query_df

con = connect()
df = query_df(con, """
SELECT code, code_type, UpHome, DownHome, ZAFPre3, Average, snapshot_time
FROM qd_sector_snapshot
WHERE snapshot_time > dateadd('m', -30, now())
ORDER BY snapshot_time DESC
LIMIT 15
""")

print(f"qd_sector_snapshot 最近 15 条:")
for i, r in df.iterrows():
    code = r['code']
    ct = r['code_type']
    up = r['UpHome']
    dn = r['DownHome']
    zaf = r['ZAFPre3']
    avg = r['Average']
    ts = r['snapshot_time']
    print(f"  {i}. {code:12s} {ct:10s} 涨{up} 跌{dn} 涨跌幅={zaf} 均价={avg} 时间={ts}")

# 检查是否有 ZTGPNum 字段
print("\n检查板块涨跌停字段:")
df2 = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_sector_snapshot'
ORDER BY ordinal_position
""")
for _, r in df2.iterrows():
    print(f"  {r['column_name']}")

con.close()