"""共振背离引擎

脚本路径: K:\QuestDB_test\\strategy\\resonance.py
用途: 多层共振分析 (大盘 + 板块 + 个股) 与背离检测, 供共振策略/背离预警策略调用
依赖: pandas, loguru, lib.relation_graph
数据源:
  - context.pricevol_df     全场价量 (含个股 Now/LastClose + 板块 Now/LastClose)
  - context.index_snapshot  上证/深证/创业板指数快照 dict
  - context.sector_flow_df  板块资金流 (net_flow 字段)
  - context.graph           关系图谱 (lib.relation_graph 内存映射)
说明:
  - 共振分数: 大盘 + 板块 + 个股同向涨 → 高分 (0-100)
  - 顶背离: 个股涨但所属板块资金净流出
  - 底背离: 个股跌但所属板块资金净流入
  - analyze 单只分析; scan_market 全场扫描返回 DataFrame
  - 板块层优先取个股所属行业板块, 取不到回退首个板块
"""

import re

import pandas as pd
from loguru import logger

from lib.relation_graph import get_stock_sectors

# 大盘指数代码
SH_INDEX = '000001.SH'   # 上证指数
SZ_INDEX = '399001.SZ'   # 深证成指
CYB_INDEX = '399006.SZ'  # 创业板指

# 个股代码正则 (6 位数字 + SH/SZ/BJ)
_STOCK_RE = re.compile(r'^\d{6}\.(SH|SZ|BJ)$')

# 背离触发阈值 (涨跌幅 %)
_DIVERGENCE_THRESHOLD = 1.0


def _safe_change(now, lastclose) -> float:
    """涨跌幅 %, 异常或除零返回 0"""
    try:
        now = float(now)
        lastclose = float(lastclose)
        if lastclose <= 0:
            return 0.0
        return (now - lastclose) / lastclose * 100
    except (TypeError, ValueError):
        return 0.0


def _index_change(index_snapshot, code) -> float:
    """从 index_snapshot 取指数涨跌幅"""
    if not index_snapshot:
        return 0.0
    snap = index_snapshot.get(code)
    if not snap:
        return 0.0
    return _safe_change(snap.get('Now'), snap.get('LastClose'))


def _sector_change(pricevol_df, block_code) -> float:
    """从 pricevol_df 取板块涨跌幅"""
    if pricevol_df is None or pricevol_df.empty or not block_code:
        return 0.0
    row = pricevol_df[pricevol_df['code'] == block_code]
    if row.empty:
        return 0.0
    r = row.iloc[0]
    return _safe_change(r.get('Now'), r.get('LastClose'))


def _stock_change(pricevol_df, code) -> float:
    """从 pricevol_df 取个股涨跌幅"""
    if pricevol_df is None or pricevol_df.empty:
        return 0.0
    row = pricevol_df[pricevol_df['code'] == code]
    if row.empty:
        return 0.0
    r = row.iloc[0]
    return _safe_change(r.get('Now'), r.get('LastClose'))


def _resonance_score(mkt_chg, sec_chg, stk_chg) -> float:
    """三层共振分数 (0-100)

    三层同涨 → 90+; 两层涨 → 60; 三层同跌 → 10; 其余 → 25-40。
    叠加平均幅度加成 (上限 100)。
    """
    ups = sum(1 for x in (mkt_chg, sec_chg, stk_chg) if x > 0)
    downs = sum(1 for x in (mkt_chg, sec_chg, stk_chg) if x < 0)
    if ups == 3:
        base = 90.0
    elif ups == 2:
        base = 60.0
    elif downs == 3:
        base = 10.0
    elif downs == 2:
        base = 25.0
    else:
        base = 40.0
    avg_amp = (abs(mkt_chg) + abs(sec_chg) + abs(stk_chg)) / 3.0
    score = min(100.0, base + avg_amp * 2.0)
    return round(score, 2)


def _detect_divergence(stock_chg, sector_code, context):
    """检测个股与板块资金流的背离

    Returns:
        'top_divergence' 顶背离 / 'bottom_divergence' 底背离 / None
    """
    if not sector_code or context.sector_flow_df is None \
            or context.sector_flow_df.empty:
        return None
    row = context.sector_flow_df[context.sector_flow_df['block_code'] == sector_code]
    if row.empty:
        return None
    try:
        net_flow = float(row.iloc[0].get('net_flow', 0) or 0)
    except (TypeError, ValueError):
        net_flow = 0.0
    if stock_chg > _DIVERGENCE_THRESHOLD and net_flow < 0:
        return 'top_divergence'
    if stock_chg < -_DIVERGENCE_THRESHOLD and net_flow > 0:
        return 'bottom_divergence'
    return None


def _pick_industry_sector(stock_code, graph):
    """取个股所属行业板块 code (回退首个板块)"""
    if graph is None:
        return None
    sectors = get_stock_sectors(stock_code)
    if not sectors:
        return None
    for s in sectors:
        if s.get('sector_type') == 'industry':
            return s.get('block_code')
    return sectors[0].get('block_code')


def analyze(stock_code, context) -> dict:
    """分析个股多层共振

    Args:
        stock_code: 股票代码 (如 '000001.SZ')
        context: StrategyContext

    Returns:
        dict: {code, market_change, sector_code, sector_change, stock_change,
               resonance_score, divergence, reason}
    """
    mkt_chg = _index_change(context.index_snapshot, SH_INDEX)
    sector_code = _pick_industry_sector(stock_code, context.graph)
    sec_chg = _sector_change(context.pricevol_df, sector_code)
    stk_chg = _stock_change(context.pricevol_df, stock_code)
    score = _resonance_score(mkt_chg, sec_chg, stk_chg)
    divergence = _detect_divergence(stk_chg, sector_code, context)

    if divergence == 'top_divergence':
        reason = f'顶背离: 个股涨{stk_chg:.2f}% 但板块资金净流出'
    elif divergence == 'bottom_divergence':
        reason = f'底背离: 个股跌{stk_chg:.2f}% 但板块资金净流入'
    elif score >= 80:
        reason = f'三层共振: 大盘{mkt_chg:.2f}% 板块{sec_chg:.2f}% 个股{stk_chg:.2f}%'
    else:
        reason = f'共振一般: 大盘{mkt_chg:.2f}% 板块{sec_chg:.2f}% 个股{stk_chg:.2f}%'

    return {
        'code': stock_code,
        'market_change': round(mkt_chg, 2),
        'sector_code': sector_code,
        'sector_change': round(sec_chg, 2),
        'stock_change': round(stk_chg, 2),
        'resonance_score': score,
        'divergence': divergence,
        'reason': reason,
    }


def scan_market(pricevol_df, index_snapshot, graph) -> pd.DataFrame:
    """全场共振扫描

    Args:
        pricevol_df: 全场价量 (含个股与板块行)
        index_snapshot: 指数快照 dict
        graph: 关系图谱对象

    Returns:
        DataFrame: 列 [code, market_change, sector_code, sector_change,
                  stock_change, resonance_score, divergence, reason]
    """
    if pricevol_df is None or pricevol_df.empty:
        logger.warning('共振扫描: pricevol_df 为空')
        return pd.DataFrame()

    # 过滤个股 (排除指数代码)
    index_codes = set(index_snapshot.keys()) if index_snapshot else set()
    codes = [c for c in pricevol_df['code'].tolist()
             if _STOCK_RE.match(str(c)) and c not in index_codes]
    if not codes:
        return pd.DataFrame()

    mkt_chg = _index_change(index_snapshot, SH_INDEX)
    rows = []
    for code in codes:
        sector_code = _pick_industry_sector(code, graph)
        sec_chg = _sector_change(pricevol_df, sector_code)
        stk_chg = _stock_change(pricevol_df, code)
        score = _resonance_score(mkt_chg, sec_chg, stk_chg)
        rows.append({
            'code': code,
            'market_change': round(mkt_chg, 2),
            'sector_code': sector_code,
            'sector_change': round(sec_chg, 2),
            'stock_change': round(stk_chg, 2),
            'resonance_score': score,
            'divergence': None,  # 全场扫描不含资金流背离, 由 analyze 单算
            'reason': f'共振分数 {score}',
        })
    df = pd.DataFrame(rows)
    logger.info('共振扫描完成: {} 只, 高分(>=80) {} 只',
                len(df), (df['resonance_score'] >= 80).sum() if not df.empty else 0)
    return df
