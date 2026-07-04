"""数据资产盘点 - 语义层补充

读 data_inventory.json 骨架, 给关键字段补语义:
- category: realtime(实时快照,有时序过期失效) / history(历史,盘后可反复拉) /
            derived(计算衍生) / reference(静态映射)
- source: 数据怎么来的 (采集器/算式)
- capability: 可以怎样用
- constraint: 约束/边界
- verify_type: live_must(务必实盘验证) / eod_ok(盘后可确认) / na(看上游)
- provenance: verified(已验证有数据) / pending_intraday(待盘中) /
              empty_no_source(空,源未接) / suspect(有数据但值可疑)

用法: python scripts/data_inventory_semantics.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# === 字段语义字典 (聚焦有策略价值的关键表) ===
# 其余表/字段脚本按表名推断 category, 不写详细语义
SEMANTICS = {
    # ========== qd_money_flow (个股明暗资金, _run_money_flow→dark_money.calc_batch 衍生) ==========
    'qd_money_flow': {
        '__table_category': 'derived',
        '__table_source': 'intraday_loop._run_money_flow 从 qd_stock_snapshot 衍生 (dark_money.calc_batch)',
        'main_net': {'source': '取 snapshot.Zjl (主力净额, tqcenter 直接给)',
                     'capability': '个股主力资金净额, 判主力方向(正净流入/负净流出)',
                     'verify_type': 'eod_ok', 'provenance': 'verified',
                     'constraint': '受C8影响: Zjl在c3@T+1s行, 须合并双行; 盘后为收盘累计值'},
        'dark_money': {'source': 'snapshot.NowVol 跨帧 diff (撤单差分代理)',
                       'capability': '暗资金撤单异常检测',
                       'verify_type': 'live_must', 'provenance': 'suspect',
                       'constraint': '撤单是实时行为, 盘后NowVol不变→diff=0; 须盘中跨帧验证'},
        'pressure_diff_5level': {'source': 'Σ(Buyp_i*Buyv_i) - Σ(Sellp_i*Sellv_i) 5档加权',
                                 'capability': '买卖盘压力差, 判承接/抛压',
                                 'verify_type': 'live_must', 'provenance': 'suspect',
                                 'constraint': '⚠️ 5档在c2@T行(C8), 若取不到会算成0; 须合并双行'},
        'buy_pressure': {'source': 'Σ(Buyp_i*Buyv_i)', 'capability': '5档买盘总金额',
                         'verify_type': 'live_must', 'provenance': 'suspect', 'constraint': '同上C8'},
        'sell_pressure': {'source': 'Σ(Sellp_i*Sellv_i)', 'capability': '5档卖盘总金额',
                          'verify_type': 'live_must', 'provenance': 'suspect', 'constraint': '同上C8'},
        'net_flow': {'source': '综合: zjl + cancel*0.3 + imbalance*0.001 + wtb*fcamo*0.01',
                     'capability': '综合资金流(明+暗)', 'verify_type': 'live_must', 'provenance': 'suspect',
                     'constraint': '依赖多个实时字段, 任一C8取空则结果失真'},
        'big_order_diff': {'source': '无来源(填None)', 'capability': '无', 'provenance': 'empty_no_source'},
        'light_money': {'source': '无来源(填None)', 'capability': '无', 'provenance': 'empty_no_source'},
        'code': {}, 'flow_time': {},
    },

    # ========== qd_stock_snapshot (L2盘口, C8双形态行: c2@T快照列 + c3@T+1s intraday列) ==========
    'qd_stock_snapshot': {
        '__table_category': 'realtime',
        '__table_source': 'c2_snapshot (快照列@T) + c3_more_info intraday模式 (资金列@T+1s)',
        '__c8_note': '✅ C8已拆表修复(commit ba2bf0f): intraday列(Zjl/FCAmo/Wtb/fHSL/ZTPrice等)已拆到 qd_stock_intraday 独立表; 此处 intraday 列仅历史残留(sparse 9.9%), 不再写入, 消费方应读 qd_stock_intraday',
        # 快照列 (c2@T) — 实时, 盘后为收盘值
        'Now': {'category': 'realtime', 'source': 'c2 get_market_snapshot', 'capability': '现价',
                'verify_type': 'live_must', 'provenance': 'verified', 'constraint': 'C8快照行; 盘后=收盘价非实时'},
        'Volume': {'category': 'realtime', 'capability': '累计成交量', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Amount': {'category': 'realtime', 'capability': '累计成交额(流动性判断)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'LastClose': {'category': 'realtime', 'capability': '前收盘(算涨幅基准)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'Open': {'category': 'realtime', 'capability': '开盘价', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'Max': {'category': 'realtime', 'capability': '最高(曾触涨停判定: Max>=ZTPrice*0.999)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Min': {'category': 'realtime', 'capability': '最低', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Buyp1': {'category': 'realtime', 'capability': '买1档挂单价(5档承接)', 'verify_type': 'live_must', 'provenance': 'verified', 'constraint': 'C8快照行; 5档全在c2行'},
        'Buyv1': {'category': 'realtime', 'capability': '买1档挂单量', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Sellp1': {'category': 'realtime', 'capability': '卖1档挂单价(5档抛压)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Sellv1': {'category': 'realtime', 'capability': '卖1档挂单量', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Inside': {'category': 'realtime', 'capability': '内盘(主动卖)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Outside': {'category': 'realtime', 'capability': '外盘(主动买)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'TickDiff': {'category': 'realtime', 'capability': '笔增减', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Zangsu': {'category': 'realtime', 'capability': '涨速', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Average': {'category': 'realtime', 'capability': '均价', 'verify_type': 'live_must', 'provenance': 'verified'},
        'MA5Value': {'category': 'realtime', 'capability': '5日均线(盘中动态)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        # intraday 列 (c3@T+1s) — 资金/封单/委托, 实时
        'Zjl': {'category': 'realtime', 'source': 'c3 get_market_snapshot intraday', 'capability': '主力净额',
                'verify_type': 'live_must', 'provenance': 'verified', 'constraint': 'C8 intraday行; T+1样本采集'},
        'Zjl_HB': {'category': 'realtime', 'capability': '主力净额环比(连续性)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'FCAmo': {'category': 'realtime', 'capability': '封单额(打板结实度核心)', 'verify_type': 'live_must', 'provenance': 'verified', 'constraint': '仅涨停票有值; 盘后为收盘封单'},
        'FCb': {'category': 'realtime', 'capability': '封成比(封单/成交)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'FzAmo': {'category': 'realtime', 'capability': '主力金额', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Fzhsl': {'category': 'realtime', 'capability': '主力换手', 'verify_type': 'live_must', 'provenance': 'verified'},
        'Wtb': {'category': 'realtime', 'capability': '委托买卖比(委买/委卖)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'ZAF': {'category': 'realtime', 'capability': '涨幅%', 'verify_type': 'live_must', 'provenance': 'verified'},
        'fHSL': {'category': 'realtime', 'capability': '换手率(筹码交换充分度)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'ZTPrice': {'category': 'realtime', 'capability': '涨停价(封板判定 Now>=ZTPrice)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'DTPrice': {'category': 'realtime', 'capability': '跌停价', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'fLianB': {'category': 'realtime', 'capability': '量比(成交活跃度, ⚠️非连板数! 说明书权威)', 'verify_type': 'live_must', 'provenance': 'verified'},
        'ZTGPNum': {'category': 'realtime', 'capability': '涨停价挂单数', 'verify_type': 'live_must', 'provenance': 'suspect', 'constraint': '样本全0, 可能采集源没填或语义不同'},
        'LastStartZT': {'category': 'realtime', 'capability': '最近启动涨停', 'verify_type': 'live_must', 'provenance': 'verified'},
    },

    # ========== qd_stock_intraday (C8拆表独立, c3 intraday字段, 单行完整) ==========
    'qd_stock_intraday': {
        '__table_category': 'realtime',
        '__table_source': 'collect/c3_more_info.py intraday模式 (C8拆表 ba2bf0f 后从 qd_stock_snapshot 独立)',
        '__note': '✅ C8拆表: 15个intraday字段独立成表, 单行完整不再双形态; 含FCAmo权威涨跌停判定(>0涨停/<0跌停); 字段语义/中文/适用对象复用 spec+target 脚本自动补',
    },

    # ========== qd_stock_daily (日级历史属性, c3 daily模式, tqcenter历史, 盘后可反复拉) ==========
    'qd_stock_daily': {
        '__table_category': 'history',
        '__table_source': 'c3_more_info daily模式, tqcenter 历史字段(一次调用给N日前数据); 盘后可反复拉不过期',
        'ZTPrice': {'category': 'history', 'capability': '涨停价', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'fLianB': {'category': 'history', 'capability': '连板数', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'EverZTCount': {'category': 'history', 'capability': '历史涨停次数(股性活跃度, 次日溢价率参考)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'YearZTDay': {'category': 'history', 'capability': '今年涨停天数', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'LastZTHzNum': {'category': 'history', 'capability': '最近涨停后连板数', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ZAFYesterday': {'category': 'history', 'capability': '昨日涨幅', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ZAFPre2D': {'category': 'history', 'capability': '2日前涨幅(轨迹)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ZAFPre5': {'category': 'history', 'capability': '5日前涨幅', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ZAFPre10': {'category': 'history', 'capability': '10日前涨幅', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ZAFPre30': {'category': 'history', 'capability': '30日前涨幅', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'CJJEPre1': {'category': 'history', 'capability': '前1日成交额(承接力/流动性)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'CJJEPre3': {'category': 'history', 'capability': '前3日成交额', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'OpenAmo': {'category': 'history', 'capability': '开盘成交额', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'OpenZTBuy': {'category': 'history', 'capability': '开盘涨停买单额(开盘强势度)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'OpenZAF': {'category': 'history', 'capability': '开盘涨幅', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'Ltsz': {'category': 'history', 'capability': '流通市值(流动性/题材容量)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'Zsz': {'category': 'history', 'capability': '总市值', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'DynaPE': {'category': 'history', 'capability': '动态市盈率(趋势票估值)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'PB_MRQ': {'category': 'history', 'capability': '市净率', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'HisHigh': {'category': 'history', 'capability': '历史最高(压力位)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'HisLow': {'category': 'history', 'capability': '历史最低(支撑位)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'BetaValue': {'category': 'history', 'capability': 'Beta(波动/风险)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
    },

    # ========== qd_kline_5m / qd_kline_1m (历史K, 实时性差) ==========
    'qd_kline_5m': {
        '__table_category': 'history',
        '__table_source': 'c4 get_market_data 拉历史已收盘K + k5 用snapshot合成今天K',
        '__note': '⚠️ 主要是历史K(已收盘), 今天的靠k5合成; 5mK要等5分钟走完才有, 实时性差, 供指标计算(MACD/MA)非实时信号源',
        'close': {'category': 'history', 'capability': '收盘价(指标计算)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'high': {'category': 'history', 'capability': '最高', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'low': {'category': 'history', 'capability': '最低', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'volume': {'category': 'history', 'capability': '成交量', 'verify_type': 'eod_ok', 'provenance': 'verified'},
    },
    'qd_kline_1m': {
        '__table_category': 'history',
        '__table_source': 'c4 get_market_data + k5合成; 1mK实时性略好于5m但仍滞后1分钟',
    },

    # ========== qd_big_order (大单事件, _run_big_order 衍生, 盘后空盘中验证) ==========
    'qd_big_order': {
        '__table_category': 'derived',
        '__table_source': 'intraday_loop._run_big_order → big_order.detect_batch: snapshot相邻帧Amount差分≥100万',
        '__note': '⚠️ 当前0行: 盘后无大单(正常); 阈值100万可能太高, 主力资金规律待探讨',
        'order_type': {'category': 'derived', 'capability': 'buy/sell 主动方向', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
        'order_level': {'category': 'derived', 'capability': 'big(100万)/huge(500万)/super(1000万)', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
        'amount': {'category': 'derived', 'capability': '成交额差分', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
    },

    # ========== qd_auction_snapshot (竞价, 时段限定 9:15-9:25 / 14:57-15:00) ==========
    'qd_auction_snapshot': {
        '__table_category': 'realtime',
        '__table_source': 'auction_monitor, tqcenter 竞价数据; 仅竞价时段采集',
        '__note': '⚠️ 当前0行: 盘后无竞价(正常); 盘中9:15-9:25/14:57-15:00才有',
        'gap_pct': {'category': 'realtime', 'capability': '竞价缺口%(高开/低开)', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
        'auction_amount': {'category': 'realtime', 'capability': '竞价成交额(抢筹强度)', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
        'auction_type': {'category': 'realtime', 'capability': '竞价类型(open开盘/close收盘)', 'verify_type': 'live_must', 'provenance': 'pending_intraday'},
    },

    # ========== qd_sector_flow (板块资金流, C8连锁受害者) ==========
    'qd_sector_flow': {
        '__table_category': 'derived',
        '__table_source': 'intraday_loop._run_sector_flow: 板块内个股 Zjl/Amount 求和',
        '__note': '⚠️ main_net全空: 个股Zjl在C8另一行(intraday行)取不到→求和为空; 修C8后恢复',
        'main_net': {'category': 'derived', 'capability': '板块主力净流入', 'verify_type': 'live_must', 'provenance': 'suspect', 'constraint': 'C8连锁, 当前值全空'},
        'total_flow': {'category': 'derived', 'capability': '板块总成交额', 'verify_type': 'live_must', 'provenance': 'suspect'},
        'net_pct': {'category': 'derived', 'capability': '净流入占比%', 'verify_type': 'live_must', 'provenance': 'verified'},
    },

    # ========== qd_lhb_detail / qd_lhb_broker (龙虎榜, T+1盘后, 盘前过滤器用) ==========
    'qd_lhb_detail': {
        '__table_category': 'history',
        '__table_source': 'lhb_analyzer (未接, 0行); 龙虎榜T+1盘后公布',
        '__note': '⚠️ 用法: 不是实时信号, 是盘前过滤器(用昨天龙虎榜筛今天票): 卖出席位多→拉黑; 机构/游资买多+板块+题材→博弈溢价',
        'reason': {'category': 'history', 'capability': '上榜原因', 'verify_type': 'eod_ok', 'provenance': 'empty_no_source'},
        'net_amount': {'category': 'history', 'capability': '净买入额', 'verify_type': 'eod_ok', 'provenance': 'empty_no_source'},
        'broker_type': {'category': 'history', 'capability': '席位类型(机构/游资/营业部)', 'verify_type': 'eod_ok', 'provenance': 'empty_no_source'},
    },
    'qd_lhb_broker': {
        '__table_category': 'history',
        '__table_source': 'lhb_analyzer (未接, 0行); 营业部统计',
        '__note': '⚠️ 用法: 知名游资席位识别(hot_level), 盘前过滤用',
        'hot_level': {'category': 'history', 'capability': '席位热度(知名游资识别)', 'verify_type': 'eod_ok', 'provenance': 'empty_no_source'},
    },

    # ========== qd_indicators (技术指标, k1 衍生, 历史K算) ==========
    'qd_indicators': {
        '__table_category': 'derived',
        '__table_source': 'k1_indicators 从 qd_kline_5m 计算',
        'macd_dif': {'category': 'derived', 'capability': 'MACD DIF(趋势)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'macd_hist': {'category': 'derived', 'capability': 'MACD柱', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'ma5': {'category': 'derived', 'capability': '5周期均线', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'pressure_high': {'category': 'derived', 'capability': '20根最高(压力位)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'support_low': {'category': 'derived', 'capability': '20根最低(支撑位)', 'verify_type': 'eod_ok', 'provenance': 'verified'},
        'boll_upper': {'category': 'derived', 'capability': '布林上轨', 'verify_type': 'eod_ok', 'provenance': 'verified'},
    },

    # ========== qd_positions (持仓, 未接, 等用户定数据源) ==========
    'qd_positions': {
        '__table_category': 'reference',
        '__table_source': '未接, 等用户定持仓来源(外部券商API/手动)',
        '__note': '⚠️ 0行: 出场策略(p15/p16)和风控依赖, 待数据源',
    },

    # ========== qd_decisions / qd_signals (系统输出, 非食材) ==========
    'qd_decisions': {'__table_category': 'derived', '__table_source': '系统决策输出(非食材)'},
    'qd_signals': {'__table_category': 'derived', '__table_source': 'k2 原子信号(非食材)'},
    'qd_resonance': {'__table_category': 'derived', '__table_source': '_run_resonance 共振分析(非食材)'},

    # ========== 静态映射表 ==========
    'qd_code_registry': {'__table_category': 'reference', '__table_source': 'daily_init 代码注册'},
    'qd_map_concept_stock': {'__table_category': 'reference', '__table_source': '关系图谱(概念-个股)'},
    'qd_map_index_stock': {'__table_category': 'reference', '__table_source': '关系图谱(指数-个股)'},
    'qd_map_region_stock': {'__table_category': 'reference', '__table_source': '关系图谱(地域-个股)'},
    'qd_map_style_stock': {'__table_category': 'reference', '__table_source': '关系图谱(风格-个股)'},
    'qd_sector_meta': {'__table_category': 'reference', '__table_source': '板块元数据'},
    'qd_stock_industry': {'__table_category': 'reference', '__table_source': '个股行业'},
    'qd_sector_daily': {'__table_category': 'history', '__table_source': 'c3 daily 板块'},
    'qd_sector_snapshot': {'__table_category': 'realtime', '__table_source': 'c2 板块快照(0行待验)'},
    'qd_index_snapshot': {'__table_category': 'realtime', '__table_source': 'c2 指数快照'},
    'qd_index_daily': {'__table_category': 'history', '__table_source': 'c3 daily 指数'},
    'qd_intraday_event': {'__table_category': 'derived', '__table_source': 'intraday_engine 异动检测'},
    'qd_sentiment_snapshot_min': {'__table_category': 'derived', '__table_source': 'k3 大盘情绪分钟级'},
    'qd_sentiment_daily': {'__table_category': 'derived', '__table_source': 'k3 大盘情绪日级(0行)'},
    'qd_sentiment_event_log': {'__table_category': 'derived', '__table_source': 'k3 情绪事件(0行)'},
    'qd_signal_log': {'__table_category': 'derived', '__table_source': 'lark 推送频控日志'},
    'qd_strategy_eval': {'__table_category': 'derived', '__table_source': '策略评估(0行)'},
    'qd_pricevol': {'__table_category': 'realtime', '__table_source': 'c1 全场价量(轻量, Now/LastClose/Volume)'},
}


def main():
    inv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'data_inventory.json')
    with open(inv_path, 'r', encoding='utf-8') as f:
        inv = json.load(f)

    merged = 0
    for tbl, tinfo in inv['tables'].items():
        sem = SEMANTICS.get(tbl, {})
        # 表级语义
        tinfo['category'] = sem.get('__table_category', 'unknown')
        if '__table_source' in sem:
            tinfo['source'] = sem['__table_source']
        if '__note' in sem:
            tinfo['note'] = sem['__note']
        if '__c8_note' in sem:
            tinfo['c8_note'] = sem['__c8_note']
        # 字段级语义
        for fld in tinfo['fields']:
            fsem = sem.get(fld['name'], {})
            if fsem:
                fld.update({k: v for k, v in fsem.items() if v not in (None, '')})
                merged += 1
            # 没显式标的字段, category 默认继承表 category (若表是 reference/history/realtime)
            if 'category' not in fld and tinfo['category'] in ('reference', 'history', 'realtime'):
                fld['category'] = tinfo['category']

    # 汇总统计
    summary = {'total_tables': len(inv['tables']), 'total_fields': sum(len(t['fields']) for t in inv['tables'].values())}
    cat_count = {}
    verify_count = {}
    prov_count = {}
    for t in inv['tables'].values():
        for f in t['fields']:
            c = f.get('category', 'unknown')
            cat_count[c] = cat_count.get(c, 0) + 1
            if 'verify_type' in f:
                verify_count[f['verify_type']] = verify_count.get(f['verify_type'], 0) + 1
            if 'provenance' in f:
                prov_count[f['provenance']] = prov_count.get(f['provenance'], 0) + 1
    summary['by_category'] = cat_count
    summary['by_verify_type'] = verify_count
    summary['by_provenance'] = prov_count
    inv['summary'] = summary

    with open(inv_path, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f'已合并 {merged} 字段语义 → {inv_path}')
    print(f'汇总: {summary}')


if __name__ == '__main__':
    main()