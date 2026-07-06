"""DDL ↔ 文档表名对账 (纯文件, 不连库)

脚本路径: K:\\QuestDB_test\\scripts\\data_inventory_ddl_audit.py
用途: 对比 ddl/*.sql 的 CREATE TABLE 表名 与 MAINTENANCE.md §3 表清单,
      检测文档漂移 (DDL 有/文档缺, 或 文档有/DDL 缺), 并交叉核对
      ddl/_reset_all.py 的 DDL_FILES 数组是否覆盖所有 .sql 文件。
依赖: loguru (运行); 复用 verify_tables.parse_all_ddl 解析 DDL
      (传递依赖 psycopg2/pandas/dotenv — 见下"import 副作用"说明)
数据源: ddl/*.sql + MAINTENANCE.md §3.1~§3.10 + ddl/_reset_all.py
入库表: 无 (只读校验)
用法:
  python scripts/data_inventory_ddl_audit.py                   # 人类可读
  python scripts/data_inventory_ddl_audit.py --json            # JSON (CI)
  python scripts/data_inventory_ddl_audit.py --strict          # 任何文档漂移都返回非零
  python scripts/data_inventory_ddl_audit.py --no-reset-check  # 跳过 _reset_all.py 交叉核对
退出码:
  0 = 文档与 DDL 完全对齐 (或仅 soft 漂移且未加 --strict)
  1 = hard 漂移 (reset_check.only_on_disk: 重建会漏表) 或 (--strict 下的 soft 漂移)
  2 = 解析/设置错误 (DDL 目录缺失 / MAINTENANCE.md 缺失 / §3 章节边界找不到)

设计边界 (与 verify_tables.py 正交, 不重复):
  - 不连数据库, 不查列, 只对表名。
  - verify_tables.py 做 DDL↔DB 列级核对 (scheduler 16:00); 本脚本做 DDL↔文档 表名级核对。
  - 复用 verify_tables.parse_all_ddl 抓 CREATE TABLE (DRY)。

import 副作用 (已核实安全):
  `from verify_tables import parse_all_ddl` 会传递触发 `from lib.qdb import ...`,
  lib/qdb.py 顶层只调 load_dotenv(config/.env) (设环境变量), 不建 DB 连接。
  传递依赖 psycopg2/pandas/dotenv 在本环境均已安装 (verify_tables 日跑)。
  若未来要纯 stdlib, 可内联 _TBL_RE 的 6 行 regex 替代 parse_all_ddl。
"""

import argparse
import ast
import json
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

# Windows GBK console 不能输出 emoji/中文, 强制 UTF-8 (verify_tables.py:30-33)
try:
    sys.stdout.reconfigure(encoding='utf-8')  # py3.7+
except Exception:
    pass

# 项目根目录
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

# 兄弟脚本复用 parse_all_ddl (precedent: data_inventory_unused.py:23-24)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_tables import parse_all_ddl  # noqa: E402


# ---- 模块级 regex ----

# §3 数据行: '| <编号> | qd_xxx | ...' — 仅匹配第 2 列是 qd_ 标识符的行
# 自动跳过表头 (| # | 表名 |)、分隔行 (|---|)、§3.11 维度表 (第1列中文)、散文/代码块
_DOC_ROW_RE = re.compile(r'^\|\s*\d+\s*\|\s*(qd_\w+)\s*\|')

# §3 章节起点 (h2): '## 3. 数据库表清单'
_SEC3_START_RE = re.compile(r'^##\s+3\.\s')

# §3 章节终点: 优先停在 '### 3.11' (本节自身), 否则停在下一个 '## N.'
# 注意: 不要松绑成 ^##\s+\d — 否则会把 §3.11 自身的数据行/代码块纳入扫描
_SEC3_END_RE = re.compile(r'^(?:###\s+3\.11\b|##\s+\d+\.)\s')

# _reset_all.py 中 DDL_FILES 行项 (AST 提取失败时的降级 regex)
_RESET_ENTRY_RE = re.compile(r"""['"](\d{2}_\w+\.sql)['"]""")


def find_doc_tables(md_path: str) -> Tuple[Set[str], List[str]]:
    """扫 MAINTENANCE.md §3.1~§3.10, 返回 (表名集合, 重复表名列表)

    扫描范围: '## 3.' 起到 '### 3.11' 或下一个 '## N.' 止 (先命中者)。
    Layer 1 (章节范围) 防 §4+/§10 泄漏; Layer 2 (数据行 regex) 防 §3.11 自身内容泄漏。
    重复表名计入集合一次, 并 append 到重复列表供 WARN (不影响退出码)。

    Raises:
        FileNotFoundError: md_path 不存在
        ValueError: §3 章节起点或终点找不到 (调用方应返回退出码 2)
    """
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Layer 1: 定位 §3 范围
    start_idx = end_idx = None
    for i, line in enumerate(lines):
        if start_idx is None:
            if _SEC3_START_RE.match(line):
                start_idx = i
        else:
            if _SEC3_END_RE.match(line):
                end_idx = i
                break
    if start_idx is None:
        raise ValueError(f'{md_path}: 找不到 §3 起点 (## 3. ...)')
    if end_idx is None:
        raise ValueError(f'{md_path}: 找不到 §3 终点 (### 3.11 或 ## N.)')

    # Layer 2: 在范围内抓数据行
    seen: Set[str] = set()
    dups: List[str] = []
    for line in lines[start_idx:end_idx]:
        m = _DOC_ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in seen:
            dups.append(name)
            logger.warning('§3 文档重复登记表: {}', name)
        else:
            seen.add(name)
    return seen, dups


def find_sql_tables(ddl_dir: str) -> Set[str]:
    """复用 verify_tables.parse_all_ddl, 仅取表名 keys()"""
    return set(parse_all_ddl(ddl_dir).keys())


def find_disk_ddl_files(ddl_dir: str) -> List[str]:
    """ddl 目录下的 .sql 文件名 (sorted)"""
    return sorted(f for f in os.listdir(ddl_dir) if f.endswith('.sql'))


def load_reset_all_files(reset_all_path: str) -> Optional[List[str]]:
    """从 ddl/_reset_all.py 静态提取 DDL_FILES 数组 (AST literal_eval, 不执行模块)

    避免导入 _reset_all.py 的模块顶层副作用:
      - os.environ['QDB_HOST'...] (KeyError 风险)
      - LOG_DIR.mkdir(exist_ok=True) (创建目录)
      - logger.add(...) (加文件 sink)

    Returns:
        List[str] 提取成功; None 文件不存在 / AST+regex 均失败 (调用方降级跳过)
    """
    if not os.path.isfile(reset_all_path):
        return None
    with open(reset_all_path, 'r', encoding='utf-8') as f:
        src = f.read()
    # 优先 AST 静态提取 (精确)
    try:
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name) and tgt.id == 'DDL_FILES':
                    return list(ast.literal_eval(node.value))
    except (SyntaxError, ValueError, TypeError) as e:
        logger.warning('AST 提取 DDL_FILES 失败, 降级 regex: {}', e)
    # 降级: 整个文件文本里抓 'NN_xxx.sql' 字面量
    found = _RESET_ENTRY_RE.findall(src)
    if found:
        # 去重保序 (regex 可能重复匹配)
        seen: List[str] = []
        for x in found:
            if x not in seen:
                seen.append(x)
        return seen
    return None


def audit(ddl_dir: str, md_path: str,
          reset_all_path: Optional[str]) -> dict:
    """主对账 → 结构化结果 dict

    Returns:
        {
          sql_tables, doc_tables, both: int,
          only_sql: [...], only_doc: [...], drift: bool, doc_duplicates: [...],
          reset_check: {enabled, disk_files, list_files, only_on_disk, only_in_list} | None
        }
    """
    doc_tables, dups = find_doc_tables(md_path)
    sql_tables = find_sql_tables(ddl_dir)

    only_sql = sorted(sql_tables - doc_tables)   # DDL 有, 文档缺
    only_doc = sorted(doc_tables - sql_tables)   # 文档有, DDL 缺
    both = len(sql_tables & doc_tables)

    result: dict = {
        'sql_tables': len(sql_tables),
        'doc_tables': len(doc_tables),
        'both': both,
        'only_sql': only_sql,
        'only_doc': only_doc,
        'drift': bool(only_sql or only_doc),
        'doc_duplicates': dups,
    }

    # _reset_all.py 交叉核对 (可选)
    if reset_all_path:
        disk = set(find_disk_ddl_files(ddl_dir))
        listed = load_reset_all_files(reset_all_path)
        if listed is None:
            logger.warning('DDL_FILES 提取失败, 跳过 reset_check: {}', reset_all_path)
            result['reset_check'] = None
        else:
            listed_set = set(listed)
            result['reset_check'] = {
                'enabled': True,
                'disk_files': len(disk),
                'list_files': len(listed_set),
                'only_on_disk': sorted(disk - listed_set),   # 磁盘有列表没 → 重建漏表 (hard)
                'only_in_list': sorted(listed_set - disk),   # 列表有磁盘没 → 运行时已 warn (soft)
            }
    else:
        result['reset_check'] = None

    return result


def format_human(result: dict) -> str:
    """人类可读输出, 镜像 verify_tables.format_human 的 '=' * 60 框风格"""
    lines: List[str] = []
    n_both = result['both']
    n_only_sql = len(result['only_sql'])
    n_only_doc = len(result['only_doc'])
    lines.append('=' * 60)
    lines.append(f'  DDL ↔ 文档对账: ✅ {n_both}   '
                 f'❌ {n_only_sql} (DDL有文档缺)   ⚠️ {n_only_doc} (文档多余)')
    lines.append('=' * 60)

    # 每张表一行 (全集, 仿 verify_tables 列全部)
    doc_set = set()
    # 重建 doc 集合用于标注 (result 里没存原始 set, 这里从 only_doc 推不出 both)
    # → 直接按 only_sql / only_doc 分类, both 表用 sql_tables - only_sql
    all_tables = sorted(set(result['only_sql']) | set(result['only_doc']))
    # both 的表无法从 result 还原 (只存了 count), 这里只列漂移项, both 用汇总数体现
    for tbl in result['only_sql']:
        lines.append(f'❌ DDL有文档缺        {tbl}')
    for tbl in result['only_doc']:
        lines.append(f'⚠️ 文档多余          {tbl}')
    if not all_tables:
        lines.append(f'✅ 全部 {n_both} 张表对齐')
    lines.append('=' * 60)

    # reset_check 块
    rc = result.get('reset_check')
    if rc:
        n_disk = rc['disk_files']
        n_list = rc['list_files']
        icon = '✅' if not rc['only_on_disk'] and not rc['only_in_list'] else '⚠️'
        lines.append(f'  _reset_all.py 交叉核对: {icon} 磁盘 {n_disk} / 列表 {n_list}')
        for f in rc['only_on_disk']:
            lines.append(f'❌ 磁盘有列表没(重建漏表) {f}')
        for f in rc['only_in_list']:
            lines.append(f'⚠️ 列表有磁盘没          {f}')
        lines.append('=' * 60)
    if result.get('doc_duplicates'):
        lines.append(f'  ℹ️ 文档重复登记: {result["doc_duplicates"]}')
        lines.append('=' * 60)
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description='DDL ↔ 文档表名对账')
    ap.add_argument('--ddl-dir', default=os.path.join(_PROJ_ROOT, 'ddl'),
                    help='DDL 目录 (默认 ddl)')
    ap.add_argument('--md', default=os.path.join(_PROJ_ROOT, 'MAINTENANCE.md'),
                    help='MAINTENANCE.md 路径')
    ap.add_argument('--reset-all',
                    default=os.path.join(_PROJ_ROOT, 'ddl', '_reset_all.py'),
                    help='ddl/_reset_all.py 路径 (传空串禁用 reset_check)')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--strict', action='store_true',
                    help='soft 漂移 (only_sql/only_doc/only_in_list) 也返回非零退出码')
    ap.add_argument('--no-reset-check', action='store_true',
                    help='跳过 _reset_all.py 交叉核对')
    args = ap.parse_args()

    # 设置错误 → 退出码 2
    if not os.path.isdir(args.ddl_dir):
        msg = f'DDL 目录不存在: {args.ddl_dir}'
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({'error': msg}, ensure_ascii=False))
        return 2
    if not os.path.isfile(args.md):
        msg = f'MAINTENANCE.md 不存在: {args.md}'
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({'error': msg}, ensure_ascii=False))
        return 2

    reset_all_path: Optional[str] = args.reset_all if args.reset_all else None
    if args.no_reset_check:
        reset_all_path = None

    try:
        result = audit(args.ddl_dir, args.md, reset_all_path)
    except (FileNotFoundError, ValueError) as e:
        msg = f'对账失败: {e}'
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({'error': msg}, ensure_ascii=False))
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_human(result))

    # 退出码决策
    rc = result.get('reset_check')
    # hard 漂移 (默认失败): reset_check.only_on_disk (重建漏表, 不可恢复)
    if rc and rc['only_on_disk']:
        return 1
    # soft 漂移 (仅 --strict 失败): only_sql / only_doc / only_in_list
    if args.strict:
        if result['only_sql'] or result['only_doc']:
            return 1
        if rc and rc['only_in_list']:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
