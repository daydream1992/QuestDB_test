#!/usr/bin/env python3
"""
全景高频采集系统
采集全市场两类数据：
  1. 快照数据：88字段行情快照（tq.get_more_info），覆盖个股/板块/指数/ETF/可转债/REITs
  2. 关系数据：板块成分股归属（tq.get_stock_list_in_sector），仅板块

标的全覆盖（约8629个）：
  个股    market=5   所有A股        ~5534只
  行业一级 market=16  研究行业一级     30个
  行业二级 market=17  研究行业二级    128个
  行业三级 market=18  研究行业三级    345个
  概念    market=12  概念板块       270个
  风格    market=13  风格板块       158个
  地区    market=14  地区板块        32个
  指数    market=9   重点指数        98个（剔除与板块/个股重叠后）
  ETF    market=31  ETF基金       1643个
  可转债  market=32  可转债         334个
  REITs  market=30  REITs          93个

字段含义参考：通达信量化平台说明书/a行情类信息/获取股票更多信息.md
板块关系API：通达信量化平台说明书/c分类板块/获取板块成份股.md

盘中每5分钟采集一次，按时间戳保存为Parquet文件，形成线性时间序列

存储架构：
  市场数据模块/全景采集/
    20260709/
      全景快照_20260709_093000.parquet   # 88字段快照
      板块成分股_20260709_093005.parquet  # 板块→个股归属
"""

import sys
import os
import time
import signal
import pandas as pd
from datetime import datetime

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq
tq.initialize(__file__)

BASE_DIR = r'k:\tdxdata-master\市场数据模块\全景采集'
INTERVAL_SECONDS = 300
BATCH_LOG = 500

FIELD_MAP = {
    # 基本与形态
    'MainBusiness': '主营构成', 'SafeValue': '安全分', 'ShineValue': '亮点数',
    'ShapeValue': '短期形态+中期形态+长期形态编号', 'TPFlag': '停牌标识',
    'ZTPrice': '涨停价', 'DTPrice': '跌停价', 'HqDate': '行情日期',
    # 成交量与市值
    'fHSL': '换手率', 'fLianB': '量比', 'Wtb': '委比',
    'Zsz': '总市值（亿）', 'Ltsz': '流通市值（亿）', 'vzangsu': '量涨速',
    'Fzhsl': '分钟换手率',          # 分钟级换手率，非日换手率
    'FzAmo': '2分钟金额（万元）',    # 最近2分钟成交额，非全日成交额
    'FreeLtgb': '自由流通股本（万）',
    # 涨幅类
    'VOpenZAF': '抢筹涨幅', 'ZAF': '涨幅', 'ZAFYesterday': '昨日涨幅',
    'ZAFPre2D': '前天涨幅', 'ZAFPre5': '5日涨幅', 'ZAFPre10': '10日涨幅',
    'ZAFPre20': '20日涨幅', 'ZAFPre30': '30日涨幅', 'ZAFPre60': '60日涨幅',
    'ZAFYear': '年初至今涨幅',
    'ZAFPreMyMonth': '涨幅（本月来）', 'ZAFPreOneYear': '涨幅（一年来）',
    'ConZAFDateNum': '连涨天数',     # 连续上涨天数
    # 资金流向
    'Zjl': '主买净额（万元）', 'Zjl_HB': '主力净流入（万元）',
    'TotalBVol': '总买量', 'TotalSVol': '总卖量',
    'BCancel': '总撤买量', 'SCancel': '总撤卖量',
    'L2TicNum': 'L2逐笔成交数', 'L2OrderNum': 'L2逐笔委托数',
    # 涨停封板
    'FCAmo': '封单额（万元）',        # >0涨停 <0跌停 =0未封板
    'FCb': '封成比',
    'OpenAmo': '开盘金额（万元）',
    'OpenZTBuy': '竞价涨停买入金额（万元）',
    'OpenAmoPre1': '昨开盘金额（万元）',
    'OpenVolPre1': '昨开盘量',
    'CJJEPre1': '昨成交额（万元）',   # 昨日全日成交额
    'CJJEPre3': '3日成交额（万元）',  # 前3日成交额之和
    'FDEPre1': '昨封单额（万元）', 'FDEPre2': '前封单额（万元）',
    'ZTGPNum': '板块指数的涨停家数',  # 仅板块有效
    'LastStartZT': '几天',           # 距上次涨停的天数
    'LastZTHzNum': '几板',           # 当前连板数（几板）
    'EverZTCount': '连板天',          # 历史最大连板天数
    'YearZTDay': '年涨停天数',
    # 价格与估值
    'MA5Value': '5日均价', 'HisHigh': '52周最高', 'HisLow': '52周最低',
    'IPO_Price': '发行价',
    'More_YJL': 'ETF, LOF溢价率',    # 仅ETF/LOF有效
    'BetaValue': '贝塔系数',
    'DynaPE': '动态市盈率',
    'MorePE': '市盈率（港股：动，其他扩展：静）',
    'StaticPE_TTM': '市盈率（TTM）', 'DYRatio': '股息率', 'PB_MRQ': '市净率（MRQ）',
    # 类型标识
    'IsT0Fund': '是否是T+0基金',
    'IsZCZGP': '是否是注册制A股',    # 1=科创板/创业板注册制
    'IsKzz': '是否是可转债',
    'Kzz_HSCode': '可转债对应的正股代码',  # 仅可转债有效
    'QHMainYYMM': '主力合约关联的月份（期货），主力和次主力',  # 仅期货有效
    'Yield': '应计利息（债券），占款天数（回购）',  # 多义：债券/回购含义不同
    # 财务指标
    'KfEarnMoney': '扣非净利润（万元）', 'RDInputFee': '研发费用（万元）',
    'CashZJ': '货币资金（万元）', 'PreReceiveZJ': '合同负债（万元）',
    'OtherQYJzc': '其它权益工具（万元）', 'StaffNum': '员工人数',
    # 关键日期（值=0表示无相关事件，格式多为YYYYMMDD）
    'RecentGGJYDate': '最近北上大额交易日', 'RecentHGDate': '最近回购预案日',
    'RecentIncentDate': '最近股权激励预案日', 'NoticeDate_Recent': '最近业绩预告日',
    'RecentReleaseDate': '最近解禁日', 'RecentDZDate': '最近定增日',
    'ReportDate': '最近财报公告日期', 'ZTDate_Recent': '近2年最近涨停板日期',
    'DTDate_Recent': '近2年最近跌停板日期', 'TopDate_Recent': '近2年最近龙虎榜日期',
    'StopJYDate_Recent': '最近停牌日期',
}

BLOCK_CATEGORY_MAP = {}
BLOCK_NAME_MAP = {}

MARKET_INDEX_CODES = []
INDEX_NAME_MAP = {}

ETF_CODES = []
ETF_NAME_MAP = {}
KZZ_CODES = []
KZZ_NAME_MAP = {}
REITS_CODES = []
REITS_NAME_MAP = {}

# 板块分类API映射。注意：dict按声明顺序处理，行业二级排在行业三级之后，
# 这样36个重叠板块会被行业二级覆盖（归为行业二级）。
API_MARKET_MAP = {
    '16': '行业一级',
    '18': '行业三级',   # 先填，被后续行业二级覆盖36个重叠
    '17': '行业二级',   # 后填，覆盖与行业三级的36个重叠板块
    '12': '概念',
    '13': '风格',
    '14': '地区',
}

NON_STOCK_API = {
    '9':  ('指数',   'MARKET_INDEX_CODES', 'INDEX_NAME_MAP'),
    '31': ('ETF',    'ETF_CODES',          'ETF_NAME_MAP'),
    '32': ('可转债', 'KZZ_CODES',          'KZZ_NAME_MAP'),
    '30': ('REITs',  'REITS_CODES',        'REITS_NAME_MAP'),
}

# tq.get_stock_list(market, ...) 的 market 参数完整参考表
# 仅供查阅，本脚本实际只使用 API_MARKET_MAP 和 NON_STOCK_API 中列出的 market。
STOCK_LIST_MARKET_PARAMS = {
    '0':  '自选股',           '1':  '持仓股',
    '5':  '所有A股',          '6':  '上证指数成份股',
    '7':  '上证主板',         '8':  '深证主板',
    '9':  '重点指数',         '10': '所有板块指数',
    '11': '缺省行业板块',     '12': '概念板块',
    '13': '风格板块',         '14': '地区板块',
    '15': '缺省行业分类+概念', '16': '研究行业一级',
    '17': '研究行业二级',     '18': '研究行业三级',
    '21': '含H股',            '22': '含可转债',
    '23': '沪深300',          '24': '中证500',
    '25': '中证1000',         '26': '国证2000',
    '27': '中证2000',         '28': '中证A500',
    '30': 'REITs',            '31': 'ETF基金',
    '32': '可转债',           '33': 'LOF基金',
    '34': '所有可交易基金',   '35': '所有沪深基金',
    '36': 'T+0基金',          '49': '金融类企业',
    '50': '沪深A股',          '51': '创业板',
    '52': '科创板',           '53': '北交所',
    '91': 'ETF追踪的指数',    '92': '国内期货主力合约',
    '101': '国内期货',        '102': '港股',
    '103': '美股',
}


def load_classification():
    """从API获取各分类板块列表"""
    global BLOCK_CATEGORY_MAP, BLOCK_NAME_MAP

    cat_counts = {}
    for market, label in API_MARKET_MAP.items():
        try:
            result = tq.get_stock_list(market, list_type=1)
            if result:
                for item in result:
                    BLOCK_CATEGORY_MAP[item['Code']] = label
                    BLOCK_NAME_MAP[item['Code']] = item.get('Name', '')
                cat_counts[label] = len(result)
        except Exception as e:
            print(f"API获取{label}(market={market})失败: {e}")
            cat_counts[label] = 0

    print(f"板块分类加载完成(API): {sum(cat_counts.values())}个板块")
    print(f"分类分布: {cat_counts}")


def load_non_stocks():
    """从API获取指数/ETF/可转债/REITs列表"""
    global MARKET_INDEX_CODES, INDEX_NAME_MAP
    global ETF_CODES, ETF_NAME_MAP
    global KZZ_CODES, KZZ_NAME_MAP
    global REITS_CODES, REITS_NAME_MAP

    globals_map = {
        'MARKET_INDEX_CODES': [], 'INDEX_NAME_MAP': {},
        'ETF_CODES': [], 'ETF_NAME_MAP': {},
        'KZZ_CODES': [], 'KZZ_NAME_MAP': {},
        'REITS_CODES': [], 'REITS_NAME_MAP': {},
    }

    for market, (label, codes_var, names_var) in NON_STOCK_API.items():
        try:
            result = tq.get_stock_list(market, list_type=1)
            if result:
                codes = [item['Code'] for item in result]
                names = {item['Code']: item['Name'] for item in result}
                globals_map[codes_var] = codes
                globals_map[names_var] = names
                print(f"{label}加载完成(market={market}): {len(codes)}个")
        except Exception as e:
            print(f"API获取{label}(market={market})失败: {e}")

    MARKET_INDEX_CODES = globals_map['MARKET_INDEX_CODES']
    INDEX_NAME_MAP = globals_map['INDEX_NAME_MAP']
    ETF_CODES = globals_map['ETF_CODES']
    ETF_NAME_MAP = globals_map['ETF_NAME_MAP']
    KZZ_CODES = globals_map['KZZ_CODES']
    KZZ_NAME_MAP = globals_map['KZZ_NAME_MAP']
    REITS_CODES = globals_map['REITS_CODES']
    REITS_NAME_MAP = globals_map['REITS_NAME_MAP']


def classify_code(code):
    """根据API分类映射判断标的类型"""
    code = str(code)
    if code in BLOCK_CATEGORY_MAP:
        return BLOCK_CATEGORY_MAP[code]
    if code in MARKET_INDEX_CODES:
        return '指数'
    if code in ETF_CODES:
        return 'ETF'
    if code in KZZ_CODES:
        return '可转债'
    if code in REITS_CODES:
        return 'REITs'
    return '个股'


def get_all_targets():
    """用API获取全市场标的列表
    全部通过 tq.get_stock_list(market, list_type) 获取：
      market=5:所有A股  9:重点指数  16:研究行业一级  17:研究行业二级  18:研究行业三级
      12:概念板块  13:风格板块  14:地区板块  30:REITs  31:ETF基金  32:可转债
    行业二级和三级的36个重叠板块归为行业二级(后处理覆盖)
    """
    stocks = tq.get_stock_list('5', list_type=0)

    all_sectors = list(BLOCK_CATEGORY_MAP.keys())
    sector_set = set(all_sectors)
    stock_set = set(stocks)

    all_indices = [c for c in MARKET_INDEX_CODES if c not in stock_set and c not in sector_set]
    all_etfs = [c for c in ETF_CODES if c not in stock_set and c not in sector_set]
    all_kzz = [c for c in KZZ_CODES if c not in stock_set and c not in sector_set]
    all_reits = [c for c in REITS_CODES if c not in stock_set and c not in sector_set]

    all_codes = stocks + all_sectors + all_indices + all_etfs + all_kzz + all_reits
    print(f"全市场标的: 个股{len(stocks)}只 + 板块{len(all_sectors)}个 + 指数{len(all_indices)}个 + ETF{len(all_etfs)}个 + 可转债{len(all_kzz)}个 + REITs{len(all_reits)}个 = {len(all_codes)}个")
    return all_codes


def collect_once(all_codes):
    """执行一次全景采集"""
    all_data = []
    failed = []
    start_time = time.time()
    query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for i, code in enumerate(all_codes):
        try:
            data = tq.get_more_info(code, field_list=[])
            if data:
                data['code'] = code
                data['类型'] = classify_code(code)
                data['查询时间'] = query_time
                all_data.append(data)
            else:
                failed.append((code, 'empty'))
        except Exception as e:
            failed.append((code, str(e)))

        if (i + 1) % BATCH_LOG == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(all_codes) - i - 1) / rate if rate > 0 else 0
            print(f"  进度: {i+1}/{len(all_codes)} ({rate:.1f}/秒, 剩余{remaining:.0f}秒)")

    elapsed = time.time() - start_time
    rate = len(all_codes) / elapsed if elapsed > 0 else 0
    print(f"采集完成: {len(all_data)}成功, {len(failed)}失败, 耗时{elapsed:.1f}秒 ({rate:.1f}/秒)")
    return all_data, failed


def collect_relations():
    """采集板块成分股关系数据(get_stock_list_in_sector)
    返回：板块代码、板块名称、板块类型、成分股数量、成分股代码列表
    """
    all_sectors = list(BLOCK_CATEGORY_MAP.keys())

    result = []
    failed = []
    start_time = time.time()
    query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for i, code in enumerate(all_sectors):
        try:
            stocks = tq.get_stock_list_in_sector(code, list_type=1)
            if stocks:
                stock_codes = [s['Code'] for s in stocks]
                stock_names = [s['Name'] for s in stocks]
            else:
                stock_codes = []
                stock_names = []

            result.append({
                '板块代码': code,
                '板块名称': BLOCK_NAME_MAP.get(code, ''),
                '板块类型': BLOCK_CATEGORY_MAP.get(code, ''),
                '成分股数量': len(stock_codes),
                '成分股代码': ','.join(stock_codes),
                '成分股名称': ','.join(stock_names),
                '查询时间': query_time,
            })
        except Exception as e:
            failed.append((code, str(e)))

        if (i + 1) % 100 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(all_sectors) - i - 1) / rate if rate > 0 else 0
            print(f"  关系采集进度: {i+1}/{len(all_sectors)} ({rate:.1f}/秒, 剩余{remaining:.0f}秒)")

    elapsed = time.time() - start_time
    rate = len(all_sectors) / elapsed if elapsed > 0 else 0
    print(f"关系采集完成: {len(result)}成功, {len(failed)}失败, 耗时{elapsed:.1f}秒")
    return result, failed


def save_relations(relations):
    """保存板块成分股关系数据"""
    if not relations:
        print("无关系数据可保存")
        return None

    df = pd.DataFrame(relations)

    now = datetime.now()
    date_dir = os.path.join(BASE_DIR, now.strftime('%Y%m%d'))
    os.makedirs(date_dir, exist_ok=True)

    ts = now.strftime('%Y%m%d_%H%M%S')
    filename = f'板块成分股_{ts}.parquet'
    filepath = os.path.join(date_dir, filename)
    df.to_parquet(filepath, index=False, engine='pyarrow')

    total_stocks = df['成分股数量'].sum()
    print(f"关系数据已保存: {filepath}")
    print(f"板块数: {len(df)}, 成分股总人次: {total_stocks}")
    return filepath


def save_snapshot(all_data):
    """保存快照数据到按时间分文件"""
    if not all_data:
        print("无数据可保存")
        return None

    df = pd.DataFrame(all_data)
    df = df.rename(columns=FIELD_MAP)

    now = datetime.now()
    date_dir = os.path.join(BASE_DIR, now.strftime('%Y%m%d'))
    os.makedirs(date_dir, exist_ok=True)

    ts = now.strftime('%Y%m%d_%H%M%S')
    filename = f'全景快照_{ts}.parquet'
    filepath = os.path.join(date_dir, filename)
    df.to_parquet(filepath, index=False, engine='pyarrow')

    type_counts = df['类型'].value_counts().to_dict()
    print(f"已保存: {filepath}")
    print(f"数据形状: {df.shape}")
    print(f"类型分布: {type_counts}")
    return filepath


def is_trading_time():
    """判断当前是否为交易时间段(周一至周五 9:30-11:30, 13:00-15:00)"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    if 930 <= t <= 1130 or 1300 <= t <= 1500:
        return True
    return False


def run_loop():
    """主循环：盘中每5分钟采集一次"""
    print("=" * 60)
    print("全景高频采集系统启动")
    print(f"采集间隔: {INTERVAL_SECONDS}秒 ({INTERVAL_SECONDS//60}分钟)")
    print(f"存储目录: {BASE_DIR}")
    print("=" * 60)

    load_classification()
    load_non_stocks()
    all_codes = get_all_targets()

    print(f"\n等待交易时间... (当前: {datetime.now().strftime('%H:%M:%S')})")
    collected_count = 0

    while True:
        if not is_trading_time():
            now = datetime.now()
            print(f"\r[{now.strftime('%H:%M:%S')}] 非交易时间，等待中...", end='', flush=True)
            time.sleep(30)
            continue

        print(f"\n\n=== 第{collected_count+1}次采集 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        all_data, failed = collect_once(all_codes)
        filepath = save_snapshot(all_data)

        print(f"\n--- 采集板块成分股关系 ---")
        relations, rel_failed = collect_relations()
        rel_filepath = save_relations(relations)

        collected_count += 1

        if failed:
            print(f"快照失败: {len(failed)}个")
        if rel_failed:
            print(f"关系失败: {len(rel_failed)}个")

        print(f"\n等待{INTERVAL_SECONDS}秒后进行下次采集...")
        time.sleep(INTERVAL_SECONDS)


def signal_handler(sig, frame):
    print("\n\n收到退出信号，正在关闭...")
    tq.close()
    sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    run_loop()
