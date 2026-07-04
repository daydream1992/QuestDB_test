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
    'qd_stock_snapshot': 'collect/c2_snapshot.py (C8拆表ba2bf0f后仅快照列, intraday已拆到qd_stock_intraday)',
    'qd_stock_intraday': 'collect/c3_more_info.py intraday模式 (C8拆表ba2bf0f独立)',
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


def parse_ddl():
    """解析 ddl/*.sql 的 '-- field ← 中文' 注释 (衍生表的权威来源)"""
    cn = {}
    ddl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ddl')
    for fp in sorted(glob.glob(os.path.join(ddl_dir, '*.sql'))):
        for line in open(fp, encoding='utf-8'):
            m = re.match(r'\s*--\s+(\w+)\s*←\s+(.+)', line)
            if m:
                f, c = m.group(1).strip(), m.group(2).strip()
                # 去掉括号里的来源说明后缀, 保留中文主体 (如 "主力净流入 (元)")
                cn[f] = c
    return cn


# 剩余字段 (说明书/DDL 都没有的: 时间戳/映射/量化术语/positions未接字段)
# 来源: 标准命名 + 代码算式, 每条都有据, 标 source_from=manual
REMAINING_FIELDS = {
    # 时间戳 (标准命名)
    'snapshot_time': '快照时间', 'flow_time': '资金流时间', 'decision_time': '决策时间',
    'order_time': '大单时间', 'auction_time': '竞价时间', 'calc_time': '计算时间',
    'event_time': '事件时间', 'kline_time': 'K线时间', 'log_time': '日志时间',
    'update_time': '更新时间', 'last_push_time': '上次推送时间', 'eval_time': '评估时间',
    'lhb_date': '龙虎榜日期', 'first_seen': '首次发现', 'last_seen': '末次发现',
    'resonance_time': '共振时间', 'signal_time': '信号时间',
    # qd_money_flow (dark_money.calc_batch 算式, 已读代码)
    'main_net': '主力净流入(元)', 'big_order_diff': '大单差(无来源填None)',
    'dark_money': '暗资金(撤单差分)', 'light_money': '明资金(无来源填None)',
    'pressure_diff_5level': '5档压力差(买盘-卖盘)', 'buy_pressure': '5档买盘总金额',
    'sell_pressure': '5档卖盘总金额', 'net_flow': '综合资金流',
    # qd_indicators (k1 算式)
    'macd_dif': 'MACD DIF线', 'macd_dea': 'MACD DEA线', 'macd_hist': 'MACD柱(2*(DIF-DEA))',
    'pressure_high': '20根最高(压力位)', 'support_low': '20根最低(支撑位)',
    'boll_upper': '布林上轨', 'boll_mid': '布林中轨', 'boll_lower': '布林下轨',
    # qd_sector_flow
    'big_net': '超大单净流入', 'mid_net': '中单净流入', 'small_net': '小单净流入',
    'total_flow': '总成交额', 'net_pct': '净流入占比%',
    # qd_big_order
    'order_type': '订单类型(buy/sell)', 'order_level': '大单级别(big/huge/super)',
    'broker': '营业部(无L2数据填None)',
    # qd_auction_snapshot
    'auction_price': '竞价价格', 'auction_volume': '竞价成交量', 'auction_amount': '竞价成交额',
    'gap_pct': '缺口%(高开/低开)', 'auction_type': '竞价类型(open/close)', 'prev_close': '前收盘',
    # qd_decisions
    'action': '动作(buy/sell/watch/warn)', 'strategy_name': '策略名',
    'position_size': '建议仓位%', 'reason': '决策原因',
    # qd_signals
    'signal_type': '信号类型', 'signal_score': '信号评分', 'metadata': '元数据(JSON)',
    # qd_resonance
    'sector_resonance': '板块共振分', 'index_resonance': '指数共振分', 'macd_resonance': 'MACD共振分',
    'volume_resonance': '量能共振分', 'flow_resonance': '资金共振分', 'total_score': '共振总分',
    'description': '描述',
    # qd_sentiment (k3 代码)
    'emotion': '情绪标签(冰点/低迷/中性/活跃/过热)', 'emotion_order': '情绪档位(0-4)',
    'zt_cnt': '涨停数', 'dt_cnt': '跌停数', 'break_cnt': '炸板数', 'fbl': '封板率%',
    'max_lb': '最高连板', 'udr': '涨跌比', 'up_cnt': '上涨家数', 'down_cnt': '下跌家数',
    'index_zaf': '指数涨幅%', 'top_sectors': '热门板块', 'lb_tier': '连板梯队',
    'zt_cnt_max': '涨停数峰值', 'fbl_avg': '封板率均值', 'summary': '总结',
    # qd_intraday_event
    'critical': '是否关键(即时推送)',
    'event_type': '事件类型(intraday:surge_up/down/limit_seal/limit_break/capital_in/out; sentiment:turn_zt_drop/turn_udr_flip/emotion_crossing)',
    'detail': '事件详情(JSON)',
    # qd_lhb_detail
    'rank': '排名', 'buy_amount': '买入额', 'sell_amount': '卖出额', 'net_amount': '净额',
    'broker_name': '营业部名称', 'broker_type': '席位类型(机构/游资)', 'broker_label': '席位标签',
    # qd_lhb_broker
    'total_buy_30d': '30日总买入', 'total_sell_30d': '30日总卖出',
    'appear_count_30d': '30日上榜次数', 'hot_level': '席位热度等级',
    # qd_code_registry
    'tdx_code': '通达信代码', 'code_type': '代码类型(stock/sector/index)',
    'is_active': '是否活跃', 'sector_category': '板块类别',
    # qd_map_*
    'concept_name': '概念名', 'index_code': '指数代码', 'region': '地域', 'style': '风格', 'weight': '权重',
    # qd_sector_meta
    'sector_code': '板块代码', 'sector_name': '板块名', 'sector_type': '板块类型',
    'stock_count': '成份股数',
    # qd_stock_industry
    'industry_l1': '一级行业', 'industry_l2': '二级行业', 'industry_l3': '三级行业',
    # qd_positions (DDL推断, 表未接)
    'direction': '方向(long/short)', 'entry_price': '入场价', 'current_price': '现价',
    'quantity': '持仓量', 'pnl': '盈亏额', 'pnl_pct': '盈亏%',
    'stop_loss_price': '止损价', 'take_profit_price': '止盈价',
    # qd_strategy_eval (DDL推断, 表未接)
    'total_signals': '总信号数', 'win_count': '盈利次数', 'loss_count': '亏损次数',
    'win_rate': '胜率', 'total_pnl': '总盈亏', 'profit_factor': '盈亏比', 'max_drawdown': '最大回撤',
    # qd_signal_log
    'signal_count': '信号计数', 'cooldown_sec': '冷却秒数', 'pushed': '是否推送',
    # OpenZAF (说明书 grep 确认: 开盘涨幅)
    'OpenZAF': '开盘涨幅%',
}


def main():
    spec_md = parse_spec()
    spec_ddl = parse_ddl()
    # 优先级: 说明书md > DDL注释 > REMAINING(代码/标准); 只在含中文时覆盖
    field_cn = dict(REMAINING_FIELDS)
    for k, v in spec_ddl.items():
        if v and re.search(r'[一-鿿]', v):
            field_cn[k] = v
    for k, val in spec_md.items():
        cn = val.get('chinese', '')
        if cn and re.search(r'[一-鿿]', cn):
            field_cn[k] = cn
    # 大小写不敏感匹配字典
    field_cn_lower = {k.lower(): v for k, v in field_cn.items()}
    print(f'来源: 说明书md={len(spec_md)} DDL注释={len(spec_ddl)} REMAINING={len(REMAINING_FIELDS)} 合并={len(field_cn)}', file=sys.stderr)

    with open(INV_PATH, 'r', encoding='utf-8') as f:
        inv = json.load(f)

    # 补表级 producer_script
    for tbl, tinfo in inv['tables'].items():
        if tbl in TABLE_PRODUCER:
            tinfo['producer_script'] = TABLE_PRODUCER[tbl]

    # 补字段级 chinese_name + spec_source
    matched = 0
    for tbl, tinfo in inv['tables'].items():
        for fld in tinfo['fields']:
            fn = fld['name']
            # 直接匹配 (大小写不敏感, value 是中文字符串)
            cn = field_cn_lower.get(fn.lower())
            # 展开字段匹配 (Buyp1->Buyp)
            if cn is None:
                m = re.match(r'^([A-Za-z]+)\d+$', fn)
                if m:
                    base = m.group(1).lower()
                    if base in field_cn_lower:
                        cn = f'{field_cn_lower[base]}(展开{fn[-1]}档)'
            if cn:
                fld['chinese_name'] = cn
                matched += 1

    print(f'字段中文覆盖: {matched}/{sum(len(t["fields"]) for t in inv["tables"].values())}', file=sys.stderr)

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