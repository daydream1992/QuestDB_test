"""数据资产盘点 - 从通达信说明书提取权威字段中文 + 产出脚本

权威来源: docs/通达信量化平台说明书/
  - 通达信量化平台API返回字段中英文映射汇总.md (491字段, 最全)
  - a行情类信息/*.md, b财务类数据/*.md (各接口明细)

补到 inventory 每字段:
- chinese_name: 权威中文名 (说明书, 非臆测)
- spec_source: 字段来自哪个 tqcenter 接口
- producer_script: 由哪个 .py 脚本写入此表
- 用权威中文覆盖之前臆测的 capability (如 fLianB 连板数→量比)

用法: python scripts/data_inventory_spec.py
"""
import os
import re
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

SPEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'docs', '通达信量化平台说明书')
INV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'data_inventory.json')


def parse_spec():
    """解析说明书所有 md 表格, 返回 field -> {chinese, note, api, file}"""
    field_cn = {}
    files = glob.glob(os.path.join(SPEC_DIR, '**', '*.md'), recursive=True) + \
            glob.glob(os.path.join(SPEC_DIR, '*.md'))
    files = sorted(set(files))
    for fp in files:
        if os.path.basename(fp) == 'CLAUDE.md':
            continue  # DB数据库_v2 项目规范, 不属于本说明书
        current_api = ''
        fname = os.path.basename(fp)
        for line in open(fp, encoding='utf-8'):
            # 接口分节 (### 1.1 xxx 或 ## xxx)
            m = re.match(r'#+\s*[\d.]*\s*(get_\w+|获取.+)', line)
            if m:
                current_api = m.group(1).strip()
                continue
            # 表格行: | `field` | ... | (兼容2/3/4列)
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) < 2:
                continue
            f0 = cells[0].strip('`').strip()
            # 跳过表头/分隔符
            if not re.match(r'^[A-Za-z]\w*$', f0):
                continue
            if f0 in ('字段', '字段(英文)', 'field'):
                continue
            # 中文说明列按列数定位:
            #   4列 字段|默认|类型|说明  → 中文=cells[3]
            #   3列 字段|中文|说明       → 中文=cells[1], note=cells[2]
            #   2列 字段|中文            → 中文=cells[1]
            if len(cells) >= 4:
                cn, note = cells[3], cells[3]
            elif len(cells) == 3:
                cn, note = cells[1], cells[2]
            else:
                cn, note = cells[1], ''
            # 中文列必须含中文 (排除 "是" "str" 这种)
            if not re.search(r'[一-鿿]', cn):
                continue
            # 优先保留更详细的 (有说明>无说明)
            existing = field_cn.get(f0)
            new_val = {'chinese': cn, 'note': note, 'api': current_api, 'file': fname}
            if existing is None or (note and not existing.get('note')):
                field_cn[f0] = new_val
    return field_cn


# tqcenter 接口 → 写入哪个 Q 表 + 哪个脚本
API_TO_TABLES = {
    'get_market_snapshot': {
        'tables': ['qd_stock_snapshot(快照列)', 'qd_sector_snapshot', 'qd_index_snapshot'],
        'script': 'collect/c2_snapshot.py',
    },
    'get_market_more_info': {
        'tables': ['qd_stock_snapshot(intraday列)', 'qd_stock_daily', 'qd_sector_daily', 'qd_index_daily'],
        'script': 'collect/c3_more_info.py',
    },
    'get_market_data': {
        'tables': ['qd_kline_1m', 'qd_kline_5m'],
        'script': 'collect/c4_kline.py + compute/k5_kline_synth.py(今天合成)',
    },
    'get_pricevol': {
        'tables': ['qd_pricevol'],
        'script': 'collect/c1_pricevol.py',
    },
}

# 表 → 产出脚本 (衍生表/映射表, 说明书没有的)
TABLE_PRODUCER = {
    'qd_code_registry': 'runner/daily_init.py',
    'qd_pricevol': 'collect/c1_pricevol.py',
    'qd_stock_snapshot': 'collect/c2_snapshot.py(快照列@T) + collect/c3_more_info.py(intraday列@T+1s) ⚠️C8双形态行',
    'qd_stock_daily': 'collect/c3_more_info.py (daily模式)',
    'qd_sector_snapshot': 'collect/c2_snapshot.py',
    'qd_sector_daily': 'collect/c3_more_info.py',
    'qd_index_snapshot': 'collect/c2_snapshot.py',
    'qd_index_daily': 'collect/c3_more_info.py',
    'qd_kline_1m': 'collect/c4_kline.py + compute/k5_kline_synth.py(今天合成)',
    'qd_kline_5m': 'collect/c4_kline.py + compute/k5_kline_synth.py(今天合成)',
    'qd_indicators': 'compute/k1_indicators.py (从qd_kline_5m衍生)',
    'qd_signals': 'compute/k2_signals.py',
    'qd_money_flow': 'runner/intraday_loop.py _run_money_flow (strategy/dark_money.py, 从snapshot衍生)',
    'qd_big_order': 'runner/intraday_loop.py _run_big_order (strategy/big_order.py, 从snapshot衍生)',
    'qd_sector_flow': 'runner/intraday_loop.py _run_sector_flow (从snapshot聚合)',
    'qd_resonance': 'runner/intraday_loop.py _run_resonance (strategy/resonance.py)',
    'qd_decisions': 'runner/intraday_loop.py _process_decisions (策略决策输出)',
    'qd_auction_snapshot': 'runner/auction_monitor.py',
    'qd_lhb_detail': 'strategy/lhb_analyzer.py (⚠️未接, 0行)',
    'qd_lhb_broker': 'strategy/lhb_analyzer.py (⚠️未接, 0行)',
    'qd_intraday_event': 'strategy/intraday_engine.py',
    'qd_sentiment_snapshot_min': 'compute/k3_sentiment.py',
    'qd_sentiment_daily': 'compute/k3_sentiment.py (⚠️0行)',
    'qd_sentiment_event_log': 'compute/k3_sentiment.py (⚠️0行)',
    'qd_positions': '⚠️未接 (待定持仓来源: 券商API/手动)',
    'qd_signal_log': 'lib/lark.py (推送频控日志)',
    'qd_strategy_eval': '⚠️未接 (0行)',
    'qd_map_concept_stock': 'lib/relation_graph.py + runner/daily_init.py',
    'qd_map_index_stock': 'lib/relation_graph.py + runner/daily_init.py',
    'qd_map_region_stock': 'lib/relation_graph.py + runner/daily_init.py',
    'qd_map_style_stock': 'lib/relation_graph.py + runner/daily_init.py',
    'qd_sector_meta': 'runner/daily_init.py',
    'qd_stock_industry': 'runner/daily_init.py',
}


def main():
    field_cn = parse_spec()
    # 大小写不敏感匹配字典 (说明书 Open vs 库 open)
    field_cn_lower = {k.lower(): v for k, v in field_cn.items()}
    print(f'说明书提取 {len(field_cn)} 字段中文映射', file=sys.stderr)

    with open(INV_PATH, 'r', encoding='utf-8') as f:
        inv = json.load(f)

    # 补表级 producer_script
    for tbl, tinfo in inv['tables'].items():
        if tbl in TABLE_PRODUCER:
            tinfo['producer_script'] = TABLE_PRODUCER[tbl]

    # 补字段级 chinese_name + spec_source
    matched = 0
    unmatched_spec = []  # 说明书有但库没用的 (信息)
    for tbl, tinfo in inv['tables'].items():
        for fld in tinfo['fields']:
            fn = fld['name']
            # 直接匹配 (大小写不敏感)
            spec = field_cn_lower.get(fn.lower())
            # 展开字段匹配 (Buyp1->Buyp, Buyv3->Buyv)
            if spec is None:
                m = re.match(r'^([A-Za-z]+)\d+$', fn)
                if m:
                    base = m.group(1).lower()
                    if base in field_cn_lower:
                        spec = dict(field_cn_lower[base])
                        spec['chinese'] = f'{field_cn_lower[base]["chinese"]}(展开{fn[-1]}档)'
            if spec:
                fld['chinese_name'] = spec['chinese']
                fld['spec_note'] = spec.get('note', '')
                fld['spec_source'] = spec.get('api', '')
                # 权威覆盖臆测的 capability (如果 capability 和 spec_note 冲突, 优先 spec)
                if spec.get('note'):
                    fld['capability_authoritative'] = spec['note']
                matched += 1

    # 统计说明书字段覆盖率
    lib_fields = set()
    for t in inv['tables'].values():
        for f in t['fields']:
            lib_fields.add(f['name'])
    spec_in_lib = sum(1 for f in field_cn if f in lib_fields)

    print(f'库字段被说明书覆盖: {matched}/{sum(len(t["fields"]) for t in inv["tables"].values())}', file=sys.stderr)
    print(f'说明书字段在库中: {spec_in_lib}/{len(field_cn)}', file=sys.stderr)

    # 标记之前臆测错误的关键纠正
    corrections = {
        'fLianB': ('连板数(臆测❌)', '量比(权威✓)'),
        'NowVol': ('现量(臆测❌)', '现手(权威✓)'),
        'XsFlag': ('标志(臆测❌)', '小数位数(权威✓)'),
    }
    inv['field_corrections'] = [
        {'field': k, 'wrong': v[0], 'right': v[1]} for k, v in corrections.items()
    ]

    with open(INV_PATH, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f'已补权威中文+产出脚本 → {INV_PATH}', file=sys.stderr)


if __name__ == '__main__':
    main()