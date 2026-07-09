#!/usr/bin/env python3
"""导出全景快照Excel
1. 全部88个字段（采集时已按API文档原文重命名，本脚本不再二次改名）
2. 板块和个股分Sheet
3. 最后一列加中文名称映射
4. 整合板块成分股关系数据为独立Sheet
5. 内置"字段说明"Sheet，列出字段含义、单位、适用类型、备注

Sheet清单（按生成顺序）：
  指数/行业一级/行业二级/行业三级/概念/风格/地区  - 板块快照，按主力净流入降序
  ETF/可转债/REITs                                - 非股票快照，按2分钟金额降序
  个股                                            - 个股快照，按2分钟金额降序
  全部汇总                                        - 所有板块类型合并（不含个股）
  板块成分股                                      - 板块→个股归属关系
  市场汇总                                        - 按类型聚合统计
  字段说明                                        - 字段含义字典

字段含义参考：通达信量化平台说明书/a行情类信息/获取股票更多信息.md
"""
import os
import glob
import pandas as pd
import json
from datetime import datetime

BASE_DIR = 'k:/tdxdata-master/市场数据模块/全景采集/20260709'

# 自动选取最新的快照和关系parquet
snapshots = sorted(glob.glob(os.path.join(BASE_DIR, '全景快照_*.parquet')))
relations = sorted(glob.glob(os.path.join(BASE_DIR, '板块成分股_*.parquet')))

if not snapshots:
    raise FileNotFoundError(f"未找到全景快照parquet: {BASE_DIR}")
if not relations:
    raise FileNotFoundError(f"未找到板块成分股parquet: {BASE_DIR}")

parquet_path = snapshots[-1]
relation_path = relations[-1]
print(f"快照文件: {parquet_path}")
print(f"关系文件: {relation_path}")

name_map_path = 'k:/tdxdata-master/市场数据模块/市场数据/名称映射.json'
index_list_path = 'k:/tdxdata-master/市场数据模块/市场数据/指数/指数列表.json'

df = pd.read_parquet(parquet_path)
rel_df = pd.read_parquet(relation_path)

with open(name_map_path, 'r', encoding='utf-8') as f:
    name_map = json.load(f)

block_index_path = 'k:/tdxdata-master/市场数据模块/市场数据/板块索引.json'
with open(block_index_path, 'r', encoding='utf-8') as f:
    block_index = json.load(f)

INDEX_NAMES = {}
with open(index_list_path, 'r', encoding='utf-8') as f:
    index_list = json.load(f)
    INDEX_NAMES = {item['code']: item['name'] for item in index_list}

def get_cn_name(code):
    if code in name_map:
        return name_map[code].get('name', '')
    if code in block_index:
        return block_index[code].get('name', '')
    return INDEX_NAMES.get(code, '')

df['中文名称'] = df['code'].map(get_cn_name)

# 字符串字段不参与数值转换
string_cols = {
    'code', '类型', '查询时间', '中文名称', '主营构成', '行情日期',
    '最近北上大额交易日', '最近回购预案日', '最近股权激励预案日',
    '最近业绩预告日', '最近解禁日', '最近定增日', '最近财报公告日期',
    '近2年最近涨停板日期', '近2年最近跌停板日期', '近2年最近龙虎榜日期',
    '最近停牌日期', '可转债对应的正股代码',
    '主力合约关联的月份（期货），主力和次主力',
    '短期形态+中期形态+长期形态编号', '停牌标识',
}
num_cols = df.select_dtypes(include=['object']).columns.tolist()
for col in num_cols:
    if col not in string_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

ts = datetime.now().strftime('%Y%m%d_%H%M%S')
output_path = f'k:/tdxdata-master/市场数据模块/全景快照全量_{ts}.xlsx'

type_order_bk = ['指数', '行业一级', '行业二级', '行业三级', '概念', '风格', '地区']
type_order_bk = [t for t in type_order_bk if t in df['类型'].unique()]

type_order_other = ['ETF', '可转债', 'REITs']
type_order_other = [t for t in type_order_other if t in df['类型'].unique()]

# 按字段名引用（已与API文档对齐）
COL_MAIN_FLOW = '主力净流入（万元）'   # Zjl_HB，板块排序用
COL_2MIN_AMO = '2分钟金额（万元）'    # FzAmo，个股/ETF等活跃度参考
COL_ZAF = '涨幅'                      # ZAF
COL_HSL = '换手率'                    # fHSL
COL_YEST_AMO = '昨成交额（万元）'     # CJJEPre1，市场汇总用（API无当日成交额字段）

# 字段说明字典：列名 -> (原字段代码, 含义, 单位, 适用类型, 备注)
FIELD_DOCS = {
    # 系统字段
    'code': ('-', '股票/板块代码', '-', '全部', '如 600519.SH / 881001.SH'),
    '类型': ('-', '标的分类', '-', '全部', '个股/行业一级/行业二级/行业三级/概念/风格/地区/指数/ETF/可转债/REITs'),
    '查询时间': ('-', '采集时间', '-', '全部', 'YYYY-MM-DD HH:MM:SS'),
    '中文名称': ('-', '标的中文名称', '-', '全部', '由本地映射表查得'),
    # 基本与形态
    '主营构成': ('MainBusiness', '主营业务构成', '-', '个股', ''),
    '安全分': ('SafeValue', '安全分评分', '分', '个股', ''),
    '亮点数': ('ShineValue', '亮点数量', '个', '个股', ''),
    '短期形态+中期形态+长期形态编号': ('ShapeValue', '短期/中期/长期形态编号组合', '-', '个股', '6位数字编码'),
    '停牌标识': ('TPFlag', '停牌标识', '-', '全部', '0=未停牌'),
    '涨停价': ('ZTPrice', '当日涨停价', '元', '全部', ''),
    '跌停价': ('DTPrice', '当日跌停价', '元', '全部', ''),
    '行情日期': ('HqDate', '当前行情日期', 'YYYYMMDD', '全部', ''),
    # 成交量与市值
    '换手率': ('fHSL', '日换手率', '%', '全部', ''),
    '量比': ('fLianB', '量比', '-', '全部', ''),
    '委比': ('Wtb', '委比', '%', '全部', ''),
    '总市值（亿）': ('Zsz', '总市值', '亿元', '全部', ''),
    '流通市值（亿）': ('Ltsz', '流通市值', '亿元', '全部', ''),
    '量涨速': ('vzangsu', '量涨速', '-', '全部', ''),
    '分钟换手率': ('Fzhsl', '分钟级换手率（非日换手率）', '%', '全部', ''),
    '2分钟金额（万元）': ('FzAmo', '最近2分钟成交额（非全日成交额）', '万元', '全部', '个股/ETF排序用此字段'),
    '自由流通股本（万）': ('FreeLtgb', '自由流通股本', '万股', '个股', ''),
    # 涨幅类
    '抢筹涨幅': ('VOpenZAF', '竞价抢筹涨幅', '%', '全部', ''),
    '涨幅': ('ZAF', '当日涨幅', '%', '全部', ''),
    '昨日涨幅': ('ZAFYesterday', '昨日涨幅', '%', '全部', ''),
    '前天涨幅': ('ZAFPre2D', '前天涨幅', '%', '全部', ''),
    '5日涨幅': ('ZAFPre5', '近5日累计涨幅', '%', '全部', ''),
    '10日涨幅': ('ZAFPre10', '近10日累计涨幅', '%', '全部', ''),
    '20日涨幅': ('ZAFPre20', '近20日累计涨幅', '%', '全部', ''),
    '30日涨幅': ('ZAFPre30', '近30日累计涨幅', '%', '全部', ''),
    '60日涨幅': ('ZAFPre60', '近60日累计涨幅', '%', '全部', ''),
    '年初至今涨幅': ('ZAFYear', '当年年初至今累计涨幅', '%', '全部', ''),
    '涨幅（本月来）': ('ZAFPreMyMonth', '本月来累计涨幅', '%', '全部', ''),
    '涨幅（一年来）': ('ZAFPreOneYear', '近一年累计涨幅', '%', '全部', ''),
    '连涨天数': ('ConZAFDateNum', '连续上涨天数', '天', '全部', ''),
    # 资金流向
    '主买净额（万元）': ('Zjl', '主买净额', '万元', '全部', ''),
    '主力净流入（万元）': ('Zjl_HB', '主力净流入', '万元', '全部', '板块排序用此字段'),
    '总买量': ('TotalBVol', '总买量', '手', '全部', ''),
    '总卖量': ('TotalSVol', '总卖量', '手', '全部', ''),
    '总撤买量': ('BCancel', '总撤买量', '手', '全部', ''),
    '总撤卖量': ('SCancel', '总撤卖量', '手', '全部', ''),
    'L2逐笔成交数': ('L2TicNum', 'L2逐笔成交数', '笔', '全部', ''),
    'L2逐笔委托数': ('L2OrderNum', 'L2逐笔委托数', '笔', '全部', ''),
    # 涨停封板
    '封单额（万元）': ('FCAmo', '封单额', '万元', '全部', '>0涨停 <0跌停 =0未封板'),
    '封成比': ('FCb', '封单量与成交量之比', '-', '全部', ''),
    '开盘金额（万元）': ('OpenAmo', '开盘金额', '万元', 'A股和板块指数', ''),
    '竞价涨停买入金额（万元）': ('OpenZTBuy', '竞价阶段涨停买入金额', '万元', '全部', ''),
    '昨开盘金额（万元）': ('OpenAmoPre1', '昨日开盘金额', '万元', '全部', ''),
    '昨开盘量': ('OpenVolPre1', '昨日开盘量', '手', '全部', ''),
    '昨成交额（万元）': ('CJJEPre1', '昨日全日成交额', '万元', '全部', '市场汇总用此字段'),
    '3日成交额（万元）': ('CJJEPre3', '前3日成交额之和', '万元', '全部', ''),
    '昨封单额（万元）': ('FDEPre1', '昨日封单额', '万元', '全部', ''),
    '前封单额（万元）': ('FDEPre2', '前日封单额', '万元', '全部', ''),
    '板块指数的涨停家数': ('ZTGPNum', '板块内涨停个股数', '家', '板块', '仅板块有效'),
    '几天': ('LastStartZT', '距上次涨停的天数', '天', '全部', ''),
    '几板': ('LastZTHzNum', '当前连板数', '板', '全部', ''),
    '连板天': ('EverZTCount', '历史最大连板天数', '天', '全部', ''),
    '年涨停天数': ('YearZTDay', '当年涨停天数', '天', '全部', ''),
    # 价格与估值
    '5日均价': ('MA5Value', '5日移动均价', '元', '全部', ''),
    '52周最高': ('HisHigh', '52周最高价', '元', '全部', ''),
    '52周最低': ('HisLow', '52周最低价', '元', '全部', ''),
    '发行价': ('IPO_Price', 'IPO发行价', '元', '个股', ''),
    'ETF, LOF溢价率': ('More_YJL', 'ETF/LOF溢价率', '%', 'ETF/LOF', ''),
    '贝塔系数': ('BetaValue', '贝塔系数', '-', '个股', ''),
    '动态市盈率': ('DynaPE', '动态市盈率', '-', '全部', ''),
    '市盈率（港股：动，其他扩展：静）': ('MorePE', '市盈率', '-', '全部', '港股=动态 其他=静态'),
    '市盈率（TTM）': ('StaticPE_TTM', 'TTM市盈率', '-', '全部', ''),
    '股息率': ('DYRatio', '股息率', '%', '全部', ''),
    '市净率（MRQ）': ('PB_MRQ', 'MRQ市净率', '-', '全部', ''),
    # 类型标识
    '是否是T+0基金': ('IsT0Fund', '是否T+0基金', '-', '基金', '1=是 0=否'),
    '是否是注册制A股': ('IsZCZGP', '是否注册制A股', '-', '个股', '1=科创板/创业板注册制'),
    '是否是可转债': ('IsKzz', '是否可转债', '-', '全部', '1=是 0=否'),
    '可转债对应的正股代码': ('Kzz_HSCode', '可转债正股代码', '-', '可转债', '仅可转债有效'),
    '主力合约关联的月份（期货），主力和次主力': ('QHMainYYMM', '期货主力合约月份', '-', '期货', '仅期货有效'),
    '应计利息（债券），占款天数（回购）': ('Yield', '应计利息/占款天数', '-', '债券/回购', '多义：债券=应计利息 回购=占款天数'),
    # 财务指标
    '扣非净利润（万元）': ('KfEarnMoney', '扣非净利润', '万元', '个股', ''),
    '研发费用（万元）': ('RDInputFee', '研发费用', '万元', '个股', ''),
    '货币资金（万元）': ('CashZJ', '货币资金', '万元', '个股', ''),
    '合同负债（万元）': ('PreReceiveZJ', '合同负债', '万元', '个股', ''),
    '其它权益工具（万元）': ('OtherQYJzc', '其它权益工具', '万元', '个股', ''),
    '员工人数': ('StaffNum', '员工人数', '人', '个股', ''),
    # 关键日期（值=0表示无相关事件）
    '最近北上大额交易日': ('RecentGGJYDate', '最近北上资金大额交易日', 'YYYYMMDD', '个股', '0=无'),
    '最近回购预案日': ('RecentHGDate', '最近回购预案公告日', 'YYYYMMDD', '个股', '0=无'),
    '最近股权激励预案日': ('RecentIncentDate', '最近股权激励预案日', 'YYYYMMDD', '个股', '0=无'),
    '最近业绩预告日': ('NoticeDate_Recent', '最近业绩预告日', 'YYYYMMDD', '个股', '0=无'),
    '最近解禁日': ('RecentReleaseDate', '最近解禁日', 'YYYYMMDD', '个股', '0=无'),
    '最近定增日': ('RecentDZDate', '最近定增日', 'YYYYMMDD', '个股', '0=无'),
    '最近财报公告日期': ('ReportDate', '最近财报公告日', 'YYYYMMDD', '个股', ''),
    '近2年最近涨停板日期': ('ZTDate_Recent', '近2年最近涨停日', 'YYYYMMDD', '全部', '0=无'),
    '近2年最近跌停板日期': ('DTDate_Recent', '近2年最近跌停日', 'YYYYMMDD', '全部', '0=无'),
    '近2年最近龙虎榜日期': ('TopDate_Recent', '近2年最近龙虎榜日', 'YYYYMMDD', '个股', '0=无'),
    '最近停牌日期': ('StopJYDate_Recent', '最近停牌日', 'YYYYMMDD', '全部', '0=无'),
}

with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
    for t in type_order_bk:
        sub = df[df['类型'] == t].copy()
        sub = sub.sort_values(COL_MAIN_FLOW, ascending=False)
        sub.to_excel(writer, index=False, sheet_name=t)

    for t in type_order_other:
        sub = df[df['类型'] == t].copy()
        sub = sub.sort_values(COL_2MIN_AMO, ascending=False)
        sub.to_excel(writer, index=False, sheet_name=t)

    gp = df[df['类型'] == '个股'].copy()
    gp = gp.sort_values(COL_2MIN_AMO, ascending=False)
    gp.to_excel(writer, index=False, sheet_name='个股')

    bk_all = df[df['类型'].isin(type_order_bk + type_order_other)].copy()
    bk_all = bk_all.sort_values(['类型', COL_MAIN_FLOW], ascending=[True, False])
    bk_all.to_excel(writer, index=False, sheet_name='全部汇总')

    # 板块成分股关系sheet
    rel_sorted = rel_df.sort_values(['板块类型', '成分股数量'], ascending=[True, False])
    rel_sorted.to_excel(writer, index=False, sheet_name='板块成分股')

    summary = df.groupby('类型').agg(
        标的数量=('code', 'count'),
        平均涨幅百分比=(COL_ZAF, 'mean'),
        总昨成交额亿元=(COL_YEST_AMO, lambda x: x.sum() / 10000),
        总主力净流入亿元=(COL_MAIN_FLOW, lambda x: x.sum() / 10000),
        平均换手率百分比=(COL_HSL, 'mean'),
    ).round(2)
    summary.to_excel(writer, sheet_name='市场汇总')

    # 字段说明sheet
    doc_rows = []
    for col_name, (code, meaning, unit, apply_type, remark) in FIELD_DOCS.items():
        doc_rows.append({
            '字段名': col_name,
            '原字段代码': code,
            '含义': meaning,
            '单位': unit,
            '适用类型': apply_type,
            '备注': remark,
        })
    doc_df = pd.DataFrame(doc_rows)
    doc_df.to_excel(writer, index=False, sheet_name='字段说明')

    # 列宽自适应
    sheets_data = {
        '板块成分股': rel_df,
        '字段说明': doc_df,
    }
    for sheet_name in writer.sheets:
        worksheet = writer.sheets[sheet_name]
        src_df = sheets_data.get(sheet_name, df)
        for i, col in enumerate(src_df.columns):
            max_len = max(
                len(str(col)),
                src_df[col].astype(str).map(len).max() if len(src_df) > 0 else 0
            )
            worksheet.set_column(i, i, min(max_len + 2, 30))

print(f'全景快照全量已导出: {output_path}')
print(f'总字段数: {len(df.columns)}')
print(f'\n各Sheet数据量:')
for t in type_order_bk + type_order_other:
    print(f'  {t}: {len(df[df["类型"]==t])}行')
print(f'  个股: {len(df[df["类型"]=="个股"])}行')
print(f'  全部汇总: {len(df[df["类型"].isin(type_order_bk + type_order_other)])}行 (所有板块类型合并，不含个股)')
print(f'  板块成分股: {len(rel_df)}个板块, 成分股总人次: {rel_df["成分股数量"].sum()}')
print(f'  市场汇总: 按类型聚合 {len(summary)}行')
print(f'  字段说明: {len(doc_df)}个字段')
print(f'\n列名(最后5列):')
for col in df.columns[-5:]:
    print(f'  {col}')
