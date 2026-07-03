"""字段写死定义 - 3 类标的 × 2 频率

映射来源: tqcenter API (get_pricevol / get_market_snapshot / get_more_info)
入库目标: QuestDB 表 (qd_*_daily / qd_*_snapshot)
"""

# === pricevol (3字段, 全场 10s) ===
PRICEVOL_FIELDS = ['LastClose', 'Now', 'Volume']

# === snapshot 个股 (32字段) ===
STOCK_SNAPSHOT_FIELDS = [
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Jjjz', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3',
    'Buyp', 'Buyv', 'Sellp', 'Sellv',
]

# === snapshot 板块/指数 (21字段, 无5档) ===
SECTOR_SNAPSHOT_FIELDS = [
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3', 'Jjjz',
]

# === more_info 个股日级 (50字段) ===
STOCK_DAILY_FIELDS = [
    'HqDate', 'ZTPrice', 'DTPrice',
    'ZAFYesterday', 'ZAFPre2D', 'ZAFPre5', 'ZAFPre10', 'ZAFPre20',
    'ZAFPre30', 'ZAFPre60', 'ZAFYear', 'ZAFPreMyMonth', 'ZAFPreOneYear',
    'Zsz', 'Ltsz', 'DynaPE', 'MorePE', 'StaticPE_TTM', 'DYRatio',
    'PB_MRQ', 'Yield', 'FreeLtgb', 'BetaValue',
    'fLianB', 'LastZTHzNum', 'EverZTCount', 'YearZTDay', 'ConZAFDateNum',
    'LastStartZT', 'ZTGPNum',
    'OpenAmo', 'OpenAmoPre1', 'OpenVolPre1', 'CJJEPre1', 'CJJEPre3',
    'FDEPre1', 'FDEPre2', 'OpenZTBuy', 'OpenZAF', 'VOpenZAF',
    'MA5Value', 'Wtb', 'HisHigh', 'HisLow',
    'MainBusiness', 'IPO_Price', 'SafeValue', 'ShineValue', 'ShapeValue',
]

# === more_info 板块日级 (15字段) ===
SECTOR_DAILY_FIELDS = [
    'HqDate', 'ZAFYesterday', 'ZAFPre5', 'ZAFPre10', 'ZAFPre20',
    'ZTGPNum', 'fLianB', 'LastStartZT', 'EverZTCount', 'YearZTDay',
    'OpenAmoPre1', 'CJJEPre1', 'CJJEPre3', 'FDEPre1', 'FDEPre2',
]

# === more_info 指数日级 (10字段) ===
INDEX_DAILY_FIELDS = [
    'HqDate', 'ZAFYesterday', 'ZAFPre5', 'ZAFPre10',
    'ZAFPre20', 'ZAFPre60', 'ZAFYear', 'Zsz', 'Ltsz', 'MA5Value',
]

# === more_info 盘中高频 (个股, 15字段) ===
STOCK_INTRADAY_FIELDS = [
    'ZAF', 'ZTPrice', 'DTPrice', 'fLianB', 'ZTGPNum', 'LastStartZT',
    'MA5Value', 'Wtb', 'fHSL', 'Fzhsl', 'FzAmo',
    'Zjl', 'Zjl_HB', 'FCAmo', 'FCb', 'vzangsu',
]

# === 字段类型映射 (DDL 定义用) ===
DOUBLE_FIELDS = {
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now', 'Amount', 'Average',
    'TickDiff', 'Jjjz', 'Before5MinNow', 'Zangsu', 'ZAFPre3',
    'Buyp1', 'Buyp2', 'Buyp3', 'Buyp4', 'Buyp5',
    'Sellp1', 'Sellp2', 'Sellp3', 'Sellp4', 'Sellp5',
    'HqDate', 'ZTPrice', 'DTPrice', 'ZAF', 'ZAFYesterday', 'ZAFPre2D', 'ZAFPre5',
    'ZAFPre10', 'ZAFPre20', 'ZAFPre30', 'ZAFPre60', 'ZAFYear', 'ZAFPreMyMonth',
    'ZAFPreOneYear', 'Zsz', 'Ltsz', 'DynaPE', 'MorePE', 'StaticPE_TTM', 'DYRatio',
    'PB_MRQ', 'Yield', 'FreeLtgb', 'BetaValue', 'fLianB', 'LastZTHzNum', 'EverZTCount',
    'YearZTDay', 'ConZAFDateNum', 'ZTGPNum', 'OpenAmo', 'OpenAmoPre1', 'OpenVolPre1',
    'CJJEPre1', 'CJJEPre3', 'FDEPre1', 'FDEPre2', 'OpenZTBuy', 'OpenZAF', 'VOpenZAF',
    'MA5Value', 'Wtb', 'HisHigh', 'HisLow', 'IPO_Price', 'SafeValue', 'ShineValue',
    'ShapeValue', 'vzangsu', 'fHSL', 'Fzhsl', 'FzAmo', 'Zjl', 'Zjl_HB', 'FCAmo', 'FCb',
}

BIGINT_FIELDS = {
    'Volume', 'NowVol', 'Inside', 'Outside',
    'Buyv1', 'Buyv2', 'Buyv3', 'Buyv4', 'Buyv5',
    'Sellv1', 'Sellv2', 'Sellv3', 'Sellv4', 'Sellv5',
}

INT_FIELDS = {
    'InOutFlag', 'XsFlag', 'UpHome', 'DownHome', 'ErrorId',
}

VARCHAR_FIELDS = {
    'LastStartZT', 'MainBusiness', 'HqDate',
}
