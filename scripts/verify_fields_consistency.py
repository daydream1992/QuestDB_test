#!/usr/bin/env python3
"""全局列名校验脚本 — 扫描所有模块的字段定义与 DDL 的一致性

用途:
  1. DDL 定义的表列集合 vs 代码中写入的列集合是否一致
  2. 采集模块 (c1..c6) 的写入列 vs DDL 是否一致
  3. 计算模块 (k1..k5) 的写入列 vs DDL 是否一致
  4. runner 模块的写入列 vs DDL 是否一致
  5. subscribe 的写入列 vs DDL 是否一致
  6. config/fields.py 的字段定义 vs DDL 是否一致
  7. 策略插件的 required_fields() 引用的字段是否在 ctx 构建路径中存在

用法:
  python scripts/verify_fields_consistency.py

输出:
  - 控制台打印检查结果 (PASS / MISMATCH / WARN)
  - 保存到 docs/field_consistency_report.txt 供下次核查对比

日期: 2026-07-08
"""

import ast
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

# ──────────────────────────────────────────────
# 1. DDL 解析：从 SQL 文件中提取表名→列名集合
# ──────────────────────────────────────────────

DDL_DIR = os.path.join(_PROJ_ROOT, 'ddl')

# DDL 中有些表通过 INSERT_COLUMNS 常量在代码中定义（不含 DDL 文件）
# 这里手动列出所有无独立 DDL 文件的表
MANUAL_TABLE_COLS: dict[str, list[str]] = {}

# subscribe 的列定义（compute/subscribe.py）
MANUAL_TABLE_COLS['qd_stock_snapshot'] = [
    'snapshot_time', 'code',
    'Now', 'LastClose', 'Open', 'Max', 'Min',
    'Volume', 'Amount', 'NowVol',
    'Inside', 'Outside',
    'Buyv1', 'Buyv2', 'Buyv3', 'Sellv1', 'Sellv2', 'Sellv3',
    'ZAF', 'ItemNum',
]
MANUAL_TABLE_COLS['qd_stock_intraday'] = [
    'snapshot_time', 'code',
    'ZAF', 'ZTPrice', 'DTPrice', 'fHSL', 'fLianB',
    'FzAmo', 'Zjl', 'Fzhsl', 'FCAmo', 'FCb', 'vzangsu',
]


def parse_ddl_table(sql_text: str) -> dict[str, list[str]]:
    """从 DDL SQL 中提取 CREATE TABLE 的表名列名

    Returns:
        dict[table_name, [col1, col2, ...]]
    """
    tables: dict[str, list[str]] = {}
    # 匹配 CREATE TABLE IF NOT EXISTS name (
    pattern = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(',
        re.IGNORECASE,
    )
    pos = 0
    while True:
        m = pattern.search(sql_text, pos)
        if not m:
            break
        table_name = m.group(1)
        # 从 '(' 后面开始找列定义，找到匹配的 ')'
        start = m.end()
        depth = 1
        i = start
        while i < len(sql_text) and depth > 0:
            if sql_text[i] == '(':
                depth += 1
            elif sql_text[i] == ')':
                depth -= 1
            i += 1
        if depth != 0:
            break
        block = sql_text[start:i - 1]
        cols = []
        for line in block.split('\n'):
            line = line.strip()
            if not line or line.upper().startswith('--') or line.startswith('//'):
                continue
            # 跳过 CONSTRAINT / PRIMARY / UNIQUE / INDEX / TIMESTAMP / PARTITION / DEDUP 等
            kw = line.upper().split()[0] if line.split() else ''
            if kw in ('CONSTRAINT', 'PRIMARY', 'UNIQUE', 'INDEX', 'TIMESTAMP',
                       'PARTITION', 'DEDUP', 'FOREIGN', 'CHECK', ')', 'KEY'):
                continue
            # 第一个词是列名
            col = line.split()[0] if line.split() else ''
            if col and col != ')':
                cols.append(col)
        tables[table_name] = cols
        pos = i
    return tables


def load_all_ddl() -> dict[str, list[str]]:
    """加载所有 DDL 文件，返回 {table: [col, ...]}"""
    all_tables: dict[str, list[str]] = {}
    if not os.path.isdir(DDL_DIR):
        print(f"[WARN] DDL 目录不存在: {DDL_DIR}")
        return all_tables
    for fname in sorted(os.listdir(DDL_DIR)):
        if not fname.endswith('.sql'):
            continue
        fpath = os.path.join(DDL_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            sql = f.read()
        parsed = parse_ddl_table(sql)
        for t, cols in parsed.items():
            if t in all_tables:
                print(f"  [WARN] DDL 表 {t} 在多个文件中定义（{fname}），覆盖")
            all_tables[t] = cols
    return all_tables


# ──────────────────────────────────────────────
# 2. 代码中 INSERT_COLUMNS / _COLS 的提取
# ──────────────────────────────────────────────

def extract_list_literal(source: str, var_name: str) -> list[str]:
    """从 Python 源码中提取列表字面量的字符串元素

    例如 extract_list_literal(src, 'INSERT_COLUMNS') → ['code', 'date', ...]
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    if isinstance(node.value, ast.List):
                        return [
                            elt.value for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
    return []


def extract_all_list_literals(source: str, var_names: set[str]) -> dict[str, list[str]]:
    """提取多个列表变量的值"""
    result = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in var_names:
                    if isinstance(node.value, ast.List):
                        result[target.id] = [
                            elt.value for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
    return result


# ──────────────────────────────────────────────
# 3. 策略 required_fields() 的提取
# ──────────────────────────────────────────────

def extract_required_fields(source: str) -> list[str]:
    """提取 required_fields 方法的返回值列表"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'required_fields':
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.List):
                    return [
                        elt.value for elt in sub.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
    return []


# ──────────────────────────────────────────────
# 4. 比较工具
# ──────────────────────────────────────────────

def diff_list(name: str, expected: list[str], actual: list[str], label_expected: str = 'DDL', label_actual: str = '代码'):
    """比较两个列列表，打印差异"""
    set_exp = set(expected)
    set_act = set(actual)
    missing = set_exp - set_act  # DDL 有但代码无
    extra = set_act - set_exp    # 代码有但 DDL 无
    if not missing and not extra:
        log(f"  [OK] {name}: 一致 ({len(set_exp)} 列)")
        return True
    if missing:
        log(f"  [MISS] {name}: {label_expected} 有但 {label_actual} 缺失 {len(missing)} 列:")
        for c in sorted(missing):
            log(f"       - {c}")
    if extra:
        log(f"  [EXTRA] {name}: {label_actual} 有但 {label_expected} 无 {len(extra)} 列:")
        for c in sorted(extra):
            log(f"       + {c}")
    return False


# ──────────────────────────────────────────────
# 5. 主流程
# ──────────────────────────────────────────────

REPORT_PATH = os.path.join(_PROJ_ROOT, 'docs', 'field_consistency_report.txt')
_log_lines = []


def log(msg: str):
    """打印并保存，Windows GBK 兼容"""
    safe = msg.encode('utf-8', errors='replace').decode('utf-8')
    try:
        print(safe)
    except UnicodeEncodeError:
        # 兜底：去掉非 ASCII
        print(safe.encode('ascii', errors='replace').decode('ascii'))
    _log_lines.append(safe)


def main():
    log(f"===== 全局列名校验 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) =====")
    log(f"项目根: {_PROJ_ROOT}")
    log("")

    # 5a. 加载 DDL 定义
    log("=== 1. DDL 表结构解析 ===")
    ddl_tables = load_all_ddl()
    log(f"  解析到 {len(ddl_tables)} 张表:")
    for t, cols in sorted(ddl_tables.items()):
        log(f"    {t}: {len(cols)} 列")
    log("")

    # 合并手动表
    for t, cols in MANUAL_TABLE_COLS.items():
        if t in ddl_tables:
            log(f"  [INFO] 表 {t} 已有 DDL ({len(ddl_tables[t])} 列)，手动定义将被比较")
        else:
            ddl_tables[t] = cols
            log(f"  [INFO] 表 {t} 无 DDL 文件，使用手动定义 ({len(cols)} 列)")
    log("")

    # 5b. 扫描采集模块的写入列
    log("=== 2. 采集模块 (collect/) 写入列 vs DDL ===")

    # c1_pricevol.py
    c1_path = os.path.join(_PROJ_ROOT, 'collect', 'c1_pricevol.py')
    if os.path.exists(c1_path):
        with open(c1_path, encoding='utf-8') as f:
            c1_src = f.read()
        # c1 写 qd_pricevol，列由 config/fields.py PRICEVOL_FIELDS + ['code', 'snapshot_time']
        log("  [c1_pricevol] qd_pricevol: code+snapshot_time+PRICEVOL_FIELDS(3)")
        # 列名嵌入在注释和代码中，用 fields.py 的 PRICEVOL_FIELDS 校验

    # c2_snapshot.py
    c2_path = os.path.join(_PROJ_ROOT, 'collect', 'c2_snapshot.py')
    if os.path.exists(c2_path):
        with open(c2_path, encoding='utf-8') as f:
            c2_src = f.read()
        c2_cols = extract_all_list_literals(c2_src, {'STOCK_TABLE_COLS', 'SECTOR_TABLE_COLS', 'INDEX_TABLE_COLS'})
        for var_name, cols in sorted(c2_cols.items()):
            # 推断表名
            if 'STOCK' in var_name:
                table = 'qd_stock_snapshot'
            elif 'SECTOR' in var_name:
                table = 'qd_sector_snapshot'
            elif 'INDEX' in var_name:
                table = 'qd_index_snapshot'
            else:
                continue
            if table in ddl_tables:
                diff_list(f"c2.{var_name} → {table}", ddl_tables[table], cols)
            else:
                log(f"  ⚠️  c2.{var_name} → {table}: DDL 未定义此表")
    else:
        log("  [SKIP] c2_snapshot.py 不存在")

    # c3_more_info.py
    c3_path = os.path.join(_PROJ_ROOT, 'collect', 'c3_more_info.py')
    if os.path.exists(c3_path):
        with open(c3_path, encoding='utf-8') as f:
            c3_src = f.read()
        # STOCK_DAILY_COLS, SECTOR_DAILY_COLS, INDEX_DAILY_COLS 在 c3 中拼装
        # 它们在 c3 中是: ['code', 'date'] + STOCK_DAILY_FIELDS
        # 我们直接参考 DDL 定义
        log("  [c3_more_info] 写入列由 config/fields.py 的 _FIELDS + ['code','date'/'snapshot_time'] 拼装")
        # 检查 daily 表
        for table, field_prefix in [('qd_stock_daily', 'STOCK_DAILY_FIELDS'),
                                     ('qd_sector_daily', 'SECTOR_DAILY_FIELDS'),
                                     ('qd_index_daily', 'INDEX_DAILY_FIELDS')]:
            if table in ddl_tables:
                log(f"    {table}: DDL 有 {len(ddl_tables[table])} 列（字段由 {field_prefix} 定义）")

        # intraday 写入
        # c3 intraday 写 qd_stock_intraday（stock）/ qd_sector_daily（sector）/ qd_index_daily（index）
        c3_intra = extract_all_list_literals(c3_src, {'_INTRADAY_COLS'})
        # _INTRADAY_COLS 是个 dict，AST 解析不了 dict 字面量非 simple 的。从源码 grep
        log("  [c3_more_info] intraday 模式: stock → qd_stock_intraday")
        if 'qd_stock_intraday' in ddl_tables:
            # 从源码提取 stock intraday 列
            intra_stock_match = re.search(r"'stock':\s*\[([^\]]+)\]", c3_src)
            if intra_stock_match:
                intra_stock_cols = re.findall(r"'(\w+)'", intra_stock_match.group(1))
                if intra_stock_cols:
                    diff_list("c3._INTRADAY_COLS[stock] → qd_stock_intraday",
                              ddl_tables['qd_stock_intraday'], intra_stock_cols)
    else:
        log("  [SKIP] c3_more_info.py 不存在")

    # c4_kline.py
    c4_path = os.path.join(_PROJ_ROOT, 'collect', 'c4_kline.py')
    if os.path.exists(c4_path):
        with open(c4_path, encoding='utf-8') as f:
            c4_src = f.read()
        c4_cols = extract_list_literal(c4_src, 'KLINE_COLS')
        log("  [c4_kline] qd_kline_1m/qd_kline_5m")
        # c4 KLINE_COLS 是 ['code', 'kline_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount']
        for table in ['qd_kline_1m', 'qd_kline_5m']:
            if table in ddl_tables:
                diff_list(f"c4.KLINE_COLS → {table}", ddl_tables[table], c4_cols)
            else:
                log(f"    ⚠️ DDL 无 {table}")
    else:
        log("  [SKIP] c4_kline.py 不存在")

    # c5_gpjy.py
    c5_path = os.path.join(_PROJ_ROOT, 'collect', 'c5_gpjy.py')
    if os.path.exists(c5_path):
        with open(c5_path, encoding='utf-8') as f:
            c5_src = f.read()
        c5_cols = extract_list_literal(c5_src, 'INSERT_COLUMNS')
        if 'qd_stock_gpjy' in ddl_tables:
            diff_list("c5.INSERT_COLUMNS → qd_stock_gpjy", ddl_tables['qd_stock_gpjy'], c5_cols)
    else:
        log("  [SKIP] c5_gpjy.py 不存在")

    # c6_lhb.py
    c6_path = os.path.join(_PROJ_ROOT, 'collect', 'c6_lhb.py')
    if os.path.exists(c6_path):
        with open(c6_path, encoding='utf-8') as f:
            c6_src = f.read()
        c6_cols = extract_all_list_literals(c6_src, {'LHB_DETAIL_COLS', 'LHB_BROKER_COLS'})
        table_map = {'LHB_DETAIL_COLS': 'qd_lhb_detail', 'LHB_BROKER_COLS': 'qd_lhb_broker'}
        for var_name, cols in c6_cols.items():
            table = table_map.get(var_name, '?')
            if table in ddl_tables:
                diff_list(f"c6.{var_name} → {table}", ddl_tables[table], cols)
            else:
                log(f"  ⚠️ c6.{var_name} → {table}: DDL 未定义")
    log("")

    # 5c. 计算模块
    log("=== 3. 计算模块 (compute/) 写入列 vs DDL ===")

    for mod_name, fname, table, col_var in [
        ('k1_indicators', 'k1_indicators.py', 'qd_indicators', 'INSERT_COLUMNS'),
        ('k2_signals', 'k2_signals.py', 'qd_signals', 'INSERT_COLUMNS'),
        ('k5_kline_synth', 'k5_kline_synth.py', 'qd_kline_5m', 'KLINE_COLS'),
    ]:
        fpath = os.path.join(_PROJ_ROOT, 'compute', fname)
        if not os.path.exists(fpath):
            log(f"  [SKIP] {fname} 不存在")
            continue
        with open(fpath, encoding='utf-8') as f:
            src = f.read()
        cols = extract_list_literal(src, col_var)
        if table in ddl_tables and cols:
            diff_list(f"{mod_name}.{col_var} → {table}", ddl_tables[table], cols)

    # k3_sentiment
    k3_path = os.path.join(_PROJ_ROOT, 'compute', 'k3_sentiment.py')
    if os.path.exists(k3_path):
        with open(k3_path, encoding='utf-8') as f:
            k3_src = f.read()
        k3_cols = extract_all_list_literals(k3_src, {'_MIN_COLS', '_EVENT_COLS'})
        if '_MIN_COLS' in k3_cols and 'qd_sentiment_snapshot_min' in ddl_tables:
            diff_list("k3._MIN_COLS → qd_sentiment_snapshot_min",
                      ddl_tables['qd_sentiment_snapshot_min'], k3_cols['_MIN_COLS'])
        if '_EVENT_COLS' in k3_cols and 'qd_sentiment_event_log' in ddl_tables:
            diff_list("k3._EVENT_COLS → qd_sentiment_event_log",
                      ddl_tables['qd_sentiment_event_log'], k3_cols['_EVENT_COLS'])
    else:
        log("  [SKIP] k3_sentiment.py 不存在")

    # k4_sentiment
    k4_path = os.path.join(_PROJ_ROOT, 'compute', 'k4_sentiment.py')
    if os.path.exists(k4_path):
        with open(k4_path, encoding='utf-8') as f:
            k4_src = f.read()
        k4_cols = extract_list_literal(k4_src, '_DEEP_COLS')
        if 'qd_sentiment_deep' in ddl_tables and k4_cols:
            diff_list("k4._DEEP_COLS → qd_sentiment_deep", ddl_tables['qd_sentiment_deep'], k4_cols)

    # k4_sector_heatmap
    kh_path = os.path.join(_PROJ_ROOT, 'compute', 'k4_sector_heatmap.py')
    if os.path.exists(kh_path):
        with open(kh_path, encoding='utf-8') as f:
            kh_src = f.read()
        kh_cols = extract_list_literal(kh_src, '_HEATMAP_COLS')
        if 'qd_sector_heatmap' in ddl_tables and kh_cols:
            diff_list("k4_heatmap._HEATMAP_COLS → qd_sector_heatmap",
                      ddl_tables['qd_sector_heatmap'], kh_cols)

    # k4_ladder_tracker
    kl_path = os.path.join(_PROJ_ROOT, 'compute', 'k4_ladder_tracker.py')
    if os.path.exists(kl_path):
        with open(kl_path, encoding='utf-8') as f:
            kl_src = f.read()
        kl_cols = extract_list_literal(kl_src, '_COLS')
        if 'qd_ladder_tracker' in ddl_tables and kl_cols:
            diff_list("k4_ladder._COLS → qd_ladder_tracker",
                      ddl_tables['qd_ladder_tracker'], kl_cols)
    log("")

    # 5d. runner 模块
    log("=== 4. runner 模块 (intraday_loop) 写入列 vs DDL ===")
    loop_path = os.path.join(_PROJ_ROOT, 'runner', 'intraday_loop.py')
    if os.path.exists(loop_path):
        with open(loop_path, encoding='utf-8') as f:
            loop_src = f.read()
        loop_cols = extract_all_list_literals(loop_src, {
            '_DECISION_COLS', '_RESONANCE_COLS', '_SECTOR_FLOW_COLS',
            '_MONEY_FLOW_COLS', '_BIG_ORDER_COLS',
        })
        for var_name, cols in loop_cols.items():
            # 推断表名
            table_map = {
                '_DECISION_COLS': 'qd_decisions',
                '_RESONANCE_COLS': 'qd_resonance',
                '_SECTOR_FLOW_COLS': 'qd_sector_flow',
                '_MONEY_FLOW_COLS': 'qd_money_flow',
                '_BIG_ORDER_COLS': 'qd_big_order',
            }
            table = table_map.get(var_name)
            if table and table in ddl_tables:
                diff_list(f"intraday_loop.{var_name} → {table}", ddl_tables[table], cols)
            elif table:
                log(f"  ⚠️ intraday_loop.{var_name} → {table}: DDL 未定义")
    log("")

    # 5e. subscribe
    log("=== 5. subscribe 写入列 vs DDL ===")
    sub_path = os.path.join(_PROJ_ROOT, 'compute', 'subscribe.py')
    if os.path.exists(sub_path):
        with open(sub_path, encoding='utf-8') as f:
            sub_src = f.read()
        sub_cols = extract_all_list_literals(sub_src, {'_SNAP_COLS', '_INTRA_COLS'})
        if '_SNAP_COLS' in sub_cols:
            # subscribe 写 qd_stock_snapshot 只包含 20 个关键字段（不是全部 DDL 列）
            log(f"  [INFO] subscribe._SNAP_COLS 写 qd_stock_snapshot ({len(sub_cols['_SNAP_COLS'])} 列, 非全量)")
            # 检查 subscribe 的列是否都在 DDL 中
            if 'qd_stock_snapshot' in ddl_tables:
                ddl_set = set(ddl_tables['qd_stock_snapshot'])
                extra = [c for c in sub_cols['_SNAP_COLS'] if c not in ddl_set]
                if extra:
                    log(f"  ❌ subscribe._SNAP_COLS 有 DDL 不存在的列: {extra}")
                else:
                    log(f"  ✅ subscribe._SNAP_COLS → qd_stock_snapshot: 所有列在 DDL 中")
        if '_INTRA_COLS' in sub_cols:
            if 'qd_stock_intraday' in ddl_tables:
                diff_list("subscribe._INTRA_COLS → qd_stock_intraday",
                          ddl_tables['qd_stock_intraday'], sub_cols['_INTRA_COLS'])

    # 5f. config/fields.py DOUBLE_FIELDS vs DDL
    log("")
    log("=== 6. config/fields.py 类型映射 vs DDL ===")
    fields_path = os.path.join(_PROJ_ROOT, 'config', 'fields.py')
    if os.path.exists(fields_path):
        with open(fields_path, encoding='utf-8') as f:
            fields_src = f.read()
        # 提取 DOUBLE_FIELDS / BIGINT_FIELDS / INT_FIELDS / VARCHAR_FIELDS
        type_sets = extract_all_list_literals(fields_src,
                                              {'DOUBLE_FIELDS', 'BIGINT_FIELDS', 'INT_FIELDS', 'VARCHAR_FIELDS'})

        # DDL 中所有数值列（排除 VARCHAR/元数据列）
        _SKIP_COLS = {'code', 'snapshot_time', 'kline_time', 'calc_time', 'signal_time',
                      'decision_time', 'flow_time', 'order_time', 'event_time', 'date',
                      'resonance_time', 'auction_time', 'update_time', 'log_time',
                      'eval_time', 'lhb_date', 'HqDate', 'first_seen', 'last_seen',
                      'entry_time', 'close_time', 'updated_time',
                      'stock_name', 'reason', 'description', 'metadata', 'detail',
                      'strategy_name', 'signal_type', 'action', 'status', 'direction',
                      'order_type', 'order_level', 'broker', 'broker_name', 'broker_type',
                      'broker_label', 'sector_code', 'sector_name', 'sector_type',
                      'code_type', 'market', 'tdx_code', 'name', 'sector_category',
                      'industry_l1', 'industry_l2', 'industry_l3', 'region', 'style',
                      'concept_name', 'index_code',
                      'last_start_zt', 'main_business',
                      'emotion', 'event_type', 'auction_type', 'signal_type',
                      'cycle_phase', 'pg_signal', 'bb_signal', 'rotation_signal',
                      'ladder_signal', 'top_sectors', 'lb_tier',
                      'divergences', 'lb_tiers', 'promotion_rankings',
                      'sector_resonance', 'stats',
                      'industry_l1_ranking', 'industry_l2_ranking',
                      'industry_l3_ranking', 'concept_ranking',
                      'industry_l1_stocks', 'industry_l2_stocks',
                      'industry_l3_stocks', 'concept_stocks',
                      'top_factors', 'MainBusiness', 'LastStartZT', 'HqDate',
                      'last_zt_time', 'close_reason', 'reason_id',
                  }

        # 收集所有 DDL 表的非 VARCHAR 列名
        uncovered: dict[str, list[str]] = {}
        for t, cols in ddl_tables.items():
            for c in cols:
                if c in _SKIP_COLS:
                    continue
                in_double = c in type_sets.get('DOUBLE_FIELDS', set())
                in_bigint = c in type_sets.get('BIGINT_FIELDS', set())
                in_int = c in type_sets.get('INT_FIELDS', set())
                in_varchar = c in type_sets.get('VARCHAR_FIELDS', set())
                if not any([in_double, in_bigint, in_int, in_varchar]):
                    uncovered.setdefault(t, []).append(c)
        if uncovered:
            for t, cols in sorted(uncovered.items()):
                log(f"  [MISS] {t} 下列不在 config/fields.py 类型映射中: {cols}")
        else:
            log(f"  [OK] 所有 DDL 数据列均在 config/fields.py 类型映射中")
    else:
        log("  [SKIP] config/fields.py 不存在")
    log("")

    # 5g. 策略插件 required_fields → DDL 列名校验
    log("=== 7. 策略插件 required_fields 字段在 DDL 中的可用性 ===")
    plugins_dir = os.path.join(_PROJ_ROOT, 'strategy', 'plugins')

    # 聚合所有 DDL 表的列名（用于反查 required_fields 是否能在某张表中找到）
    ddl_columns: set[str] = set()
    ddl_table_map: dict[str, set[str]] = {}
    for t, cols in ddl_tables.items():
        s = set(cols)
        ddl_table_map[t] = s
        ddl_columns.update(s)

    all_req_fields: dict[str, list[str]] = {}
    if os.path.isdir(plugins_dir):
        for fname in sorted(os.listdir(plugins_dir)):
            if not fname.endswith('.py') or fname.startswith('_'):
                continue
            fpath = os.path.join(plugins_dir, fname)
            with open(fpath, encoding='utf-8') as f:
                src = f.read()
            req = extract_required_fields(src)
            if req:
                all_req_fields[fname] = req
                missing_in_ddl = [f for f in req if f not in ddl_columns]
                if missing_in_ddl:
                    log(f"  [MISS] {fname} required_fields 不在任何 DDL 表中: {missing_in_ddl}")
                else:
                    # 找出这些字段可能在哪张表中
                    candidate_tables = []
                    for t, col_set in ddl_table_map.items():
                        overlap = [f for f in req if f in col_set]
                        if overlap:
                            candidate_tables.append(f"{t}({','.join(overlap)})")
                    log(f"  [OK] {fname} {len(req)} 个字段均在 DDL 中")
                    # 打印候选表（最多 3 张）
                    if candidate_tables:
                        log(f"       -> 关联表: {'; '.join(candidate_tables[:3])}")
    else:
        log(f"  [SKIP] plugins dir 不存在: {plugins_dir}")
    log("")

    # 检查没有 required_fields 但有 context 访问的策略
    log("=== 8. 策略插件对 ctx 字段的引用检查 (基于注释/命名) ===")
    for fname in sorted(os.listdir(plugins_dir)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        if fname not in all_req_fields:
            # 没有 required_fields 的策略，检查它们实际引用了哪些 ctx 字段
            fpath = os.path.join(plugins_dir, fname)
            with open(fpath, encoding='utf-8') as f:
                src = f.read()
            used = [f for f in sorted(ddl_columns) if f in src]
            if used:
                log(f"  [INFO] {fname} 引用了 DDL 字段: 含 {len(used)} 个 (未声明 required_fields)")
    log("")

    # 汇总
    log(f"===== 校验完成 =====")
    log(f"检查了 {len(ddl_tables)} 张 DDL 表, {len(all_req_fields)} 个策略插件的 required_fields")
    log(f"详细报告已保存到: {REPORT_PATH}")

    # 保存报告
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(_log_lines))
    print(f"\n报告已保存到: {REPORT_PATH}")


if __name__ == '__main__':
    main()
