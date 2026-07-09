"""recover_tables: rebuild QuestDB table registry from DDL files

When tables.d is overwritten, partition data dirs still exist
but QuestDB doesn't recognize them. This script re-runs the
CREATE TABLE statements from ddl/*.sql to re-register them.
"""

import sys, os, glob, re

# 加入项目路径以便 import lib.qdb
sys.path.insert(0, r'K:\QuestDB_test')

DDL_DIR = r'K:\QuestDB_test\ddl'
DB_DIR = r'K:\QuestDB\bin\qdbroot\db'


def parse_ddl_files():
    """从 DDL 文件解析 CREATE TABLE 语句

    Returns:
        dict: {table_name: "CREATE TABLE ... DDL语句"}
    """
    sql_files = sorted(glob.glob(os.path.join(DDL_DIR, '[0-9]*.sql')))
    table_ddls = {}

    for sf in sql_files:
        fname = os.path.basename(sf)
        with open(sf, 'r', encoding='utf-8') as f:
            content = f.read()

        # 去掉 /* */ 注释
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

        # 逐行处理: 去掉行内 -- 注释（仅当不在字符串中）
        lines = []
        for line in content.split('\n'):
            # 去掉行内 -- 注释
            in_string = False
            quote_char = None
            for i, ch in enumerate(line):
                if ch in ("'", '"'):
                    if not in_string:
                        in_string = True
                        quote_char = ch
                    elif ch == quote_char:
                        in_string = False
                elif ch == '-' and i + 1 < len(line) and line[i+1] == '-':
                    if not in_string:
                        line = line[:i]
                        break
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

        clean = '\n'.join(lines)

        # 按 ';' 分割取第一条 CREATE TABLE
        for stmt in clean.split(';'):
            stmt = stmt.strip()
            if not stmt or 'CREATE TABLE' not in stmt.upper():
                continue
            stmt = stmt + ';'
            m = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', stmt, re.IGNORECASE)
            if m:
                tname = m.group(1)
                table_ddls[tname] = stmt

    return table_ddls


def main():
    from lib.qdb import connect
    from loguru import logger

    print('从 DDL 文件提取表结构...')
    table_ddls = parse_ddl_files()
    print(f'解析到 {len(table_ddls)} 张表:\n')
    for t in sorted(table_ddls.keys()):
        print(f'  {t}')
    print()

    con = connect()
    cur = con.cursor()
    ok = fail = 0
    for name, ddl in sorted(table_ddls.items()):
        try:
            cur.execute(ddl)
            print(f'  [OK] {name}')
            ok += 1
        except Exception as e:
            print(f'  [FAIL] {name}: {str(e)[:120]}')
            fail += 1
    cur.close()

    # 验证
    import pandas as pd
    tables = pd.read_sql_query(
        "SELECT table_name, table_row_count FROM tables() "
        "WHERE table_name LIKE 'qd_%' "
        "AND table_name NOT LIKE '%_v2' AND table_name NOT LIKE 'qd_kline\\_%' "
        "ORDER BY table_name",
        con
    )
    print(f'\n共 {len(tables)} 张表（{ok} 成功, {fail} 失败）:')
    for _, r in tables.iterrows():
        cnt = r.get('table_row_count', 0)
        if cnt and cnt > 0:
            print(f'  {r["table_name"]:30s} {cnt:>10,.0f} 行')
        else:
            print(f'  {r["table_name"]}')
    con.close()

    if fail:
        print(f'\n!! {fail} tables failed, check DDL inline comments')
    else:
        print('\nAll 40 tables created successfully')


if __name__ == '__main__':
    main()
