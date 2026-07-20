"""给 qd_decisions 加 stock_name 列"""
import sys
sys.path.insert(0, '.')
from loguru import logger
from lib.qdb import connect

con = connect()
cur = con.cursor()

try:
    cur.execute("ALTER TABLE qd_decisions ADD COLUMN stock_name VARCHAR")
    con.commit()
    print("OK: qd_decisions.stock_name 列已添加")
except Exception as e:
    if 'already exists' in str(e) or 'duplicate' in str(e).lower():
        print("OK: stock_name 列已存在")
    else:
        print(f"ERROR: {e}")

con.close()