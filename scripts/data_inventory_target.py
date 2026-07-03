"""数据资产盘点 - 补字段/表适用对象 (个股/板块/指数/共用)

权威来源: config/fields.py 的 STOCK_/SECTOR_/INDEX_ 字段分组 (采集时按对象分组)
混用会出错: 板块取5档=空, 个股取上涨家数=无, 板块取主力净额=无。

apply_target 取值:
- stock: 个股专属 (5档/主力资金/换手/估值/日级独有)
- sector: 板块专属
- index: 指数专属
- stock_sector: 个股+板块共用 (基础价量/涨跌家数)
- universal: 个股+板块+指数都有 (code/时间戳/HqDate等)
- market: 大盘级 (情绪表)
- mapping: 映射表 (关系图谱)
- system/strategy: 系统/策略级

用法: python scripts/data_inventory_target.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import fields as F  # config/fields.py

INV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'data_inventory.json')

# 表级适用对象 (基于表语义)
TABLE_TARGET = {
    'qd_pricevol': 'stock', 'qd_stock_snapshot': 'stock', 'qd_stock_daily': 'stock',
    'qd_sector_snapshot': 'sector', 'qd_sector_daily': 'sector',
    'qd_sector_flow': 'sector', 'qd_sector_meta': 'sector',
    'qd_index_snapshot': 'index', 'qd_index_daily': 'index',
    'qd_kline_1m': 'stock', 'qd_kline_5m': 'stock', 'qd_indicators': 'stock',
    'qd_money_flow': 'stock', 'qd_big_order': 'stock',
    'qd_signals': 'stock', 'qd_decisions': 'stock', 'qd_auction_snapshot': 'stock',
    'qd_lhb_detail': 'stock', 'qd_lhb_broker': 'stock', 'qd_positions': 'stock',
    'qd_intraday_event': 'stock', 'qd_resonance': 'stock', 'qd_stock_industry': 'stock',
    'qd_sentiment_snapshot_min': 'market', 'qd_sentiment_daily': 'market',
    'qd_sentiment_event_log': 'market',
    'qd_code_registry': 'universal (stock+sector+index 注册表)',
    'qd_map_concept_stock': 'mapping (stock↔concept)',
    'qd_map_index_stock': 'mapping (stock↔index)',
    'qd_map_region_stock': 'mapping (stock↔region)',
    'qd_map_style_stock': 'mapping (stock↔style)',
    'qd_strategy_eval': 'strategy', 'qd_signal_log': 'system',
}


def build_field_target():
    """从 fields.py 分组推断每字段适用对象"""
    # 各分组 (snapshot 的 Buyp/Buyv 是 List, 库里展开 Buyp1-5, 用前缀匹配)
    stock_snap = set(F.STOCK_SNAPSHOT_FIELDS)
    sector_snap = set(F.SECTOR_SNAPSHOT_FIELDS)
    stock_daily = set(F.STOCK_DAILY_FIELDS)
    sector_daily = set(F.SECTOR_DAILY_FIELDS)
    index_daily = set(F.INDEX_DAILY_FIELDS)
    stock_intraday = set(F.STOCK_INTRADAY_FIELDS)

    # 一个字段出现在哪些"对象集合"
    field_in = {}  # field -> set('stock','sector','index')

    def add(group, target):
        for f in group:
            field_in.setdefault(f, set()).add(target)

    add(stock_snap, 'stock')
    add(sector_snap, 'sector')  # 板块+指数快照同字段集
    add(sector_snap, 'index')
    add(stock_daily, 'stock')
    add(sector_daily, 'sector')
    add(index_daily, 'index')
    add(stock_intraday, 'stock')  # 盘中高频只个股

    target = {}
    for f, s in field_in.items():
        if s == {'stock', 'sector', 'index'}:
            target[f] = 'universal'
        elif s == {'stock', 'sector'}:
            target[f] = 'stock_sector'
        elif s == {'stock'}:
            target[f] = 'stock'
        elif s == {'sector'}:
            target[f] = 'sector'
        elif s == {'index'}:
            target[f] = 'index'
        elif s == {'sector', 'index'}:
            target[f] = 'sector_index'
        else:
            target[f] = '/'.join(sorted(s))
    return target


# 语义注: 某些共用字段在个股/板块含义不同
SEMANTIC_NOTE = {
    'Inside': '个股=内盘(主动卖), 板块/指数=跌停家数',
    'Outside': '个股=外盘(主动买), 板块/指数=涨停家数',
    'UpHome': '个股常为0, 板块/指数=上涨家数',
    'DownHome': '个股常为0, 板块/指数=下跌家数',
    'ZTGPNum': '个股=涨停价挂单数, 板块/指数=涨停家数',
}


def main():
    field_target = build_field_target()
    # 大小写不敏感 + 展开字段前缀匹配 (Buyp1->Buyp)
    ft_lower = {k.lower(): v for k, v in field_target.items()}

    with open(INV_PATH, 'r', encoding='utf-8') as f:
        inv = json.load(f)

    # 补表级
    for tbl, tinfo in inv['tables'].items():
        if tbl in TABLE_TARGET:
            tinfo['apply_target'] = TABLE_TARGET[tbl]

    # 补字段级
    matched = 0
    for tbl, tinfo in inv['tables'].items():
        for fld in tinfo['fields']:
            fn = fld['name']
            tgt = ft_lower.get(fn.lower())
            # 展开字段 (Buyp1 -> Buyp)
            if tgt is None:
                import re
                m = re.match(r'^([A-Za-z]+)\d+$', fn)
                if m and m.group(1).lower() in ft_lower:
                    tgt = ft_lower[m.group(1).lower()]
            if tgt:
                fld['apply_target'] = tgt
                matched += 1
            # 语义注
            if fn in SEMANTIC_NOTE:
                fld['target_note'] = SEMANTIC_NOTE[fn]

    # 通用字段兜底 (code/时间戳/通用衍生) — 没标上的, 若表是 derived/mapping 则用表target
    for tbl, tinfo in inv['tables'].items():
        ttbl = tinfo.get('apply_target', '')
        for fld in tinfo['fields']:
            if 'apply_target' not in fld:
                # code/时间戳类 → universal
                if fld['name'].lower() in ('code', 'name') or 'time' in fld['name'].lower() or '_date' in fld['name'].lower():
                    fld['apply_target'] = 'universal'
                elif ttbl:
                    fld['apply_target'] = ttbl.split(' ')[0]  # 取表target首词

    uncovered = []
    for tbl, tinfo in inv['tables'].items():
        for f in tinfo['fields']:
            if 'apply_target' not in f:
                uncovered.append(f'{tbl}.{f["name"]}')
    print(f'字段适用对象: fields.py直接匹配 {matched}, 未匹配 {len(uncovered)} → {uncovered[:10]}', file=sys.stderr)

    with open(INV_PATH, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f'已补适用对象 → {INV_PATH}', file=sys.stderr)


if __name__ == '__main__':
    main()