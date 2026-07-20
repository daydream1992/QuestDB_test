"""查看 qd_decisions 表结构"""
import sys
sys.path.insert(0, '.')
from lib.qdb import connect, query_df
con = connect()
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_decisions'
ORDER BY ordinal_position
""")
for _, r in df.iterrows():
    print(r['column_name'])
con.close()