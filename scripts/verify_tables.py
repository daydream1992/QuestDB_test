"""DDL 表结构校验

脚本路径: K:\QuestDB_test\\scripts\\verify_tables.py
用途: 对比 ddl/*.sql 的 CREATE TABLE 定义与 QuestDB 实际列结构, 发现列缺失/多余
依赖: psycopg2
数据源: ddl/*.sql (DDL 源) + QuestDB information_schema.columns (实际)
入库表: 无 (只读校验)
用法:
  python scripts/verify_tables.py          # 人类可读输出
  python scripts/verify_tables.py --json   # JSON 输出 (CI 用)
  python scripts/verify_tables.py --strict # 任何 ❌/⚠️ 退出码非零

退出码:
  0 = 全部 ✅
  1 = 有 ❌ (缺列/表不存在) 或 ⚠️ (多余列)
  2 = 连接失败 / 解析失败

C1 类 bug 防范: 之前 qd_pricevol 列名 snake_case vs PascalCase 错配, 跑了整天才发现。
本脚本在 scheduler 每日 16:00 跑一次, 立即发现漂移。
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Tuple

# Windows GBK console 不能输出 emoji, 强制 UTF-8 (避免 ✅/❌/⚠️ 编码失败)
try:
    sys.stdout.reconfigure(encoding='utf-8')  # py3.7+
except Exception:
    pass

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, _exec_with_reconnect  # noqa: E402


# 匹配 CREATE TABLE [IF NOT EXISTS] qd_xxx (
_TBL_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s+TIMESTAMP',
    re.IGNORECASE | re.DOTALL,
)
# 匹配列定义 "    name TYPE"
_COL_RE = re.compile(r'^\s*(\w+)\s+(\w+)', re.MULTILINE)


def parse_ddl_file(path: str) -> Dict[str, List[str]]:
    """解析一个 .sql 文件, 返回 {table_name: [col1, col2, ...]} (按文件内顺序)

    仅解析 CREATE TABLE (忽略注释/其他 DDL)。
    """
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    out: Dict[str, List[str]] = {}
    for m in _TBL_RE.finditer(text):
        tbl = m.group(1)
        body = m.group(2)
        cols = _COL_RE.findall(body)
        if cols:
            out[tbl] = [c[0] for c in cols]
    return out


def parse_all_ddl(ddl_dir: str) -> Dict[str, List[str]]:
    """扫整个 ddl 目录, 合并所有 CREATE TABLE"""
    out: Dict[str, List[str]] = {}
    for fname in sorted(os.listdir(ddl_dir)):
        if not fname.endswith('.sql'):
            continue
        path = os.path.join(ddl_dir, fname)
        try:
            tables = parse_ddl_file(path)
        except Exception as e:
            logger.warning('解析 {} 失败: {}', path, e)
            continue
        for tbl, cols in tables.items():
            if tbl in out:
                logger.warning('表 {} 在多个 DDL 中定义, 合并 (后者覆盖)', tbl)
            out[tbl] = cols
    return out


def fetch_actual_columns(con) -> Dict[str, List[str]]:
    """从 QuestDB information_schema 拉所有 qd_ 表的实际列

    Returns:
        {table_name: [col1, col2, ...]} 按 ordinal_position 排序
    """
    sql = """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_name LIKE 'qd_%'
        ORDER BY table_name, ordinal_position
    """
    df = _exec_with_reconnect(con, lambda c: query_df(c, sql))
    if df is None or df.empty:
        return {}
    out: Dict[str, List[str]] = {}
    for _, r in df.iterrows():
        out.setdefault(r['table_name'], []).append(r['column_name'])
    return out


def verify(ddl_dir: str, con) -> Tuple[int, int, int, List[dict]]:
    """主校验

    Returns:
        (ok_count, missing_count, extra_count, issues)
        issues = [{table, kind, detail, file?}, ...]
    """
    ddl_tables = parse_all_ddl(ddl_dir)
    actual_tables = fetch_actual_columns(con)

    ok = miss = extra = 0
    issues: List[dict] = []

    # DDL 有但 DB 没有 → ❌ 缺表
    for tbl in sorted(ddl_tables.keys()):
        if tbl not in actual_tables:
            miss += 1
            issues.append({'table': tbl, 'kind': 'missing_table',
                           'detail': f'DDL 定义 {len(ddl_tables[tbl])} 列, DB 无此表'})
            continue
        ddl_cols = set(ddl_tables[tbl])
        actual_cols = set(actual_tables[tbl])
        # 缺列
        missing_cols = ddl_cols - actual_cols
        # 多余列 (DDL 没声明但 DB 存在 — 可能是历史遗留或文档漂移)
        extra_cols = actual_cols - ddl_cols
        if not missing_cols and not extra_cols:
            ok += 1
            issues.append({'table': tbl, 'kind': 'ok',
                           'detail': f'{len(actual_cols)}/{len(ddl_cols)} 列对齐'})
        else:
            if missing_cols:
                miss += 1
                issues.append({'table': tbl, 'kind': 'missing_cols',
                               'detail': f'缺列: {sorted(missing_cols)}'})
            if extra_cols:
                extra += 1
                issues.append({'table': tbl, 'kind': 'extra_cols',
                               'detail': f'多余列: {sorted(extra_cols)}'})

    # DB 有但 DDL 没定义 → ⚠️ 孤儿表 (可能是新建未补 DDL)
    for tbl in sorted(actual_tables.keys()):
        if tbl not in ddl_tables:
            extra += 1
            issues.append({'table': tbl, 'kind': 'orphan_table',
                           'detail': f'DB 有 {len(actual_tables[tbl])} 列, DDL 未定义'})

    return ok, miss, extra, issues


def format_human(issues: List[dict], ok: int, miss: int, extra: int) -> str:
    lines = []
    lines.append('=' * 60)
    lines.append(f'  DDL 校验: ✅ {ok}  ❌ {miss}  ⚠️ {extra}')
    lines.append('=' * 60)
    for it in issues:
        kind = it['kind']
        tbl = it['table']
        if kind == 'ok':
            icon = '✅'
        elif kind == 'missing_table':
            icon = '❌ 表不存在'
        elif kind == 'missing_cols':
            icon = '❌ 缺列'
        elif kind == 'extra_cols':
            icon = '⚠️ 多余列'
        elif kind == 'orphan_table':
            icon = '⚠️ 孤儿表'
        else:
            icon = '?'
        lines.append(f'{icon:20s} {tbl:25s} {it["detail"]}')
    lines.append('=' * 60)
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='DDL 表结构校验')
    ap.add_argument('--ddl-dir', default=os.path.join(_PROJ_ROOT, 'ddl'),
                    help='DDL 目录 (默认 ddl)')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--strict', action='store_true',
                    help='任何 ❌/⚠️ 都返回非零退出码 (默认仅 ❌ 返回非零)')
    args = ap.parse_args()

    if not os.path.isdir(args.ddl_dir):
        print(f'DDL 目录不存在: {args.ddl_dir}', file=sys.stderr)
        return 2

    try:
        con = connect()
    except Exception as e:
        msg = f'QuestDB 连接失败: {e}'
        if args.json:
            print(json.dumps({'error': msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    try:
        ok, miss, extra, issues = verify(args.ddl_dir, con)
    finally:
        con.close()

    if args.json:
        print(json.dumps({'ok': ok, 'missing': miss, 'extra': extra,
                          'issues': issues}, ensure_ascii=False, indent=2))
    else:
        print(format_human(issues, ok, miss, extra))

    if miss > 0:
        return 1
    if extra > 0 and args.strict:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())