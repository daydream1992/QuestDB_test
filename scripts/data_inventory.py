"""数据资产盘点 - 字段能力骨架探测

扫所有 qd_ 表的每个字段, 机器查真实数据状态 (非空率/distinct/min/max/样本),
输出 JSON 骨架。语义层 (来源/能力/边界/验证状态) 由人工/代码理解后补充。

用法: python scripts/data_inventory.py
输出: docs/data_inventory.json
"""
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from lib.qdb import connect, query_df


def _safe_stat(con, table, field, dtype):
    """查单字段统计: 非空数/distinct/min/max。失败返回部分。"""
    stat = {'non_null': None, 'distinct': None, 'min': None, 'max': None}
    try:
        r = query_df(con, f'SELECT count("{field}") AS nn, count(*) AS tot FROM {table}').iloc[0]
        stat['non_null'] = int(r['nn'])
        stat['total'] = int(r['tot'])
    except Exception:
        return stat
    if stat['non_null'] == 0 or stat.get('total') == 0:
        return stat
    # distinct
    try:
        d = query_df(con, f'SELECT count(DISTINCT "{field}") AS d FROM {table}').iloc[0]['d']
        stat['distinct'] = int(d)
    except Exception:
        pass
    # min/max (数值/时间才查, VARCHAR 跳过避免大字符串)
    if dtype in ('DOUBLE', 'FLOAT', 'INT', 'BIGINT', 'LONG', 'SHORT', 'TIMESTAMP', 'DATE'):
        try:
            mm = query_df(con, f'SELECT min("{field}") AS mn, max("{field}") AS mx FROM {table}').iloc[0]
            stat['min'] = None if mm['mn'] is None else (float(mm['mn']) if dtype in ('DOUBLE','FLOAT') else (mm['mn'].isoformat() if hasattr(mm['mn'], 'isoformat') else str(mm['mn'])))
            stat['max'] = None if mm['mx'] is None else (float(mm['mx']) if dtype in ('DOUBLE','FLOAT') else (mm['mx'].isoformat() if hasattr(mm['mx'], 'isoformat') else str(mm['mx'])))
        except Exception:
            pass
    # 样本值 (任取一个非空)
    try:
        s = query_df(con, f'SELECT "{field}" AS v FROM {table} WHERE "{field}" IS NOT NULL LIMIT 1')
        if not s.empty:
            v = s.iloc[0]['v']
            stat['sample'] = v.isoformat() if hasattr(v, 'isoformat') else (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)[:50])
    except Exception:
        pass
    return stat


def main():
    con = connect()
    # 拿所有 qd_ 表 + 字段 + 类型
    meta = query_df(con, """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_name LIKE 'qd_%'
        ORDER BY table_name, ordinal_position
    """)
    tables = {}
    for _, r in meta.iterrows():
        tbl, fld, dtype = r['table_name'], r['column_name'], r['data_type']
        tables.setdefault(tbl, {'fields': []})
        tables[tbl]['fields'].append({'name': fld, 'type': dtype})

    out = {
        'generated_at': datetime.now().isoformat(),
        'note': '骨架: 真实数据统计由脚本查, 语义层(来源/能力/边界/验证状态)待补',
        'tables': {},
    }
    for tbl, info in sorted(tables.items()):
        # 表行数
        try:
            tot = query_df(con, f'SELECT count(*) AS n FROM {tbl}').iloc[0]['n']
        except Exception as e:
            tot = -1
        t_info = {'row_count': int(tot), 'fields': []}
        print(f'扫表 {tbl} ({len(info["fields"])}字段, {tot}行)...', file=sys.stderr)
        for f in info['fields']:
            stat = _safe_stat(con, tbl, f['name'], f['type'])
            tot_f = stat.get('total', tot)
            non_null_pct = round(stat['non_null'] / tot_f * 100, 1) if (stat['non_null'] is not None and tot_f) else None
            # 数据状态判定
            if tot_f == 0:
                status = 'empty_table'
            elif non_null_pct is None:
                status = 'unknown'
            elif non_null_pct < 5:
                status = 'near_empty'
            elif non_null_pct < 80:
                status = 'sparse'
            else:
                status = 'healthy'
            t_info['fields'].append({
                'name': f['name'],
                'type': f['type'],
                'non_null_pct': non_null_pct,
                'distinct': stat['distinct'],
                'min': stat['min'],
                'max': stat['max'],
                'sample': stat.get('sample'),
                'data_status': status,
            })
        out['tables'][tbl] = t_info

    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs'), exist_ok=True)
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'data_inventory.json')
    with open(out_path, 'w', encoding='utf-8') as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print(f'\n输出: {out_path}', file=sys.stderr)
    # 摘要
    total_fields = sum(len(t['fields']) for t in out['tables'].values())
    empty = sum(1 for t in out['tables'].values() for f in t['fields'] if f['data_status'] in ('empty_table','near_empty'))
    sparse = sum(1 for t in out['tables'].values() for f in t['fields'] if f['data_status'] == 'sparse')
    healthy = sum(1 for t in out['tables'].values() for f in t['fields'] if f['data_status'] == 'healthy')
    print(f'共 {len(out["tables"])} 表 {total_fields} 字段: healthy={healthy} sparse={sparse} near_empty/empty={empty}', file=sys.stderr)
    con.close()


if __name__ == '__main__':
    main()