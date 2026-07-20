"""验证所有修复"""
import sys
sys.path.insert(0, '.')
import ast

# 语法检查
files = [
    'K:/QuestDB_test/runner/intraday_loop.py',
    'K:/QuestDB_test/strategy/plugins/p06_resonance.py',
    'K:/QuestDB_test/strategy/plugins/p08_dark_money.py',
    'K:/QuestDB_test/strategy/plugins/p17_market_emotion.py',
]

for f in files:
    try:
        with open(f, encoding='utf-8') as fp:
            ast.parse(fp.read())
        print(f"  OK: {f}")
    except Exception as e:
        print(f"  FAIL: {f} -> {e}")

print()

# 验证 stock_name 列已添加
from lib.qdb import connect, query_df
con = connect()
df = query_df(con, """
SELECT column_name FROM information_schema.columns
WHERE table_name = 'qd_decisions'
ORDER BY ordinal_position
""")
print("qd_decisions 字段:")
for _, r in df.iterrows():
    marker = " <- NEW" if r['column_name'] == 'stock_name' else ""
    print(f"  {r['column_name']}{marker}")

# 验证 get_stock_name 工作
from lib.relation_graph import get_stock_name
test_codes = ['600519.SH', '000001.SZ', '002479.SZ', '999999.XZ']
print("\nget_stock_name 测试:")
for code in test_codes:
    name = get_stock_name(code)
    print(f"  {code} -> {name or '(unknown)'}")

con.close()