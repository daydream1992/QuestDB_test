"""板块资金流监控

脚本路径: K:\QuestDB_test\\strategy\\sector_flow.py
用途: 聚合板块内个股资金流, 检测板块轮动, 识别资金流与价格的背离
依赖: pandas, loguru
数据源:
  - 个股 more_info 盘中字段 (Zjl 主力净额, Amount 成交额, Now/LastClose 价格)
  - sector_flow_history 板块资金流历史 (calc_sector_flow 产出的 list[dict])
说明:
  - calc_sector_flow 聚合单板块: 总成交额 / 主力净流入 / 涨跌家数 / 均涨幅 / 强度
  - detect_rotation 比较历史最近两期: 净流入增大→流入加速, 净流出增大→流出加速
  - flow_divergence 价格涨但资金净流出 → 顶背离; 价格跌但资金净流入 → 底背离
"""

import pandas as pd
from loguru import logger


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _change_pct(now, lastclose) -> float:
    try:
        now = float(now)
        lastclose = float(lastclose)
        if lastclose <= 0:
            return 0.0
        return (now - lastclose) / lastclose * 100
    except (TypeError, ValueError):
        return 0.0


def calc_sector_flow(block_code, stocks_data) -> dict:
    """聚合板块内个股资金

    Args:
        block_code: 板块代码
        stocks_data: list[dict] 板块内个股数据, 每项含
                     Zjl(主力净额) / Amount(成交额) / Now / LastClose

    Returns:
        dict: {block_code, stock_count, total_amount, net_flow, up_count,
               down_count, flat_count, avg_change, flow_strength, timestamp}
        flow_strength = net_flow / total_amount (主力净额占比)
    """
    total_amount = 0.0
    net_flow = 0.0
    up = down = flat = 0
    changes = []

    for s in stocks_data or []:
        zjl = _safe_float(s.get('Zjl'))
        amt = _safe_float(s.get('Amount'))
        chg = _change_pct(s.get('Now'), s.get('LastClose'))
        total_amount += amt
        net_flow += zjl
        changes.append(chg)
        if chg > 0:
            up += 1
        elif chg < 0:
            down += 1
        else:
            flat += 1

    avg_change = sum(changes) / len(changes) if changes else 0.0
    flow_strength = net_flow / total_amount if total_amount > 0 else 0.0

    return {
        'block_code': block_code,
        'stock_count': len(stocks_data or []),
        'total_amount': round(total_amount, 2),
        'net_flow': round(net_flow, 2),
        'up_count': up,
        'down_count': down,
        'flat_count': flat,
        'avg_change': round(avg_change, 2),
        'flow_strength': round(flow_strength, 4),
    }


def detect_rotation(sector_flow_history) -> dict:
    """检测板块轮动 (流入加速 / 流出加速)

    Args:
        sector_flow_history: list[dict] 按时间升序的板块资金流历史,
            每项含 block_code / net_flow / flow_strength / avg_change

    Returns:
        dict: {block_code, type, prev_flow, curr_flow, delta, reason}
        type ∈ {'inflow_accelerate', 'outflow_accelerate', 'inflow_decelerate',
                'outflow_decelerate', 'stable'}; 数据不足返回 type='insufficient'
    """
    if not sector_flow_history or len(sector_flow_history) < 2:
        return {'block_code': None, 'type': 'insufficient',
                'reason': '历史不足两期'}

    prev = sector_flow_history[-2]
    curr = sector_flow_history[-1]
    prev_flow = _safe_float(prev.get('net_flow'))
    curr_flow = _safe_float(curr.get('net_flow'))
    delta = curr_flow - prev_flow
    block_code = curr.get('block_code') or prev.get('block_code')

    if curr_flow >= 0 and delta > 0:
        rtype = 'inflow_accelerate'
        reason = f'流入加速: {prev_flow:.0f} → {curr_flow:.0f}'
    elif curr_flow < 0 and delta < 0:
        rtype = 'outflow_accelerate'
        reason = f'流出加速: {prev_flow:.0f} → {curr_flow:.0f}'
    elif curr_flow >= 0 and delta < 0:
        rtype = 'inflow_decelerate'
        reason = f'流入减速: {prev_flow:.0f} → {curr_flow:.0f}'
    elif curr_flow < 0 and delta > 0:
        rtype = 'outflow_decelerate'
        reason = f'流出减速: {prev_flow:.0f} → {curr_flow:.0f}'
    else:
        rtype = 'stable'
        reason = f'资金流稳定: {curr_flow:.0f}'

    return {
        'block_code': block_code,
        'type': rtype,
        'prev_flow': round(prev_flow, 2),
        'curr_flow': round(curr_flow, 2),
        'delta': round(delta, 2),
        'reason': reason,
    }


def flow_divergence(block_code, context) -> dict:
    """板块资金流与板块价格背离

    Args:
        block_code: 板块代码
        context: StrategyContext (用 pricevol_df 取板块价格,
                 sector_flow_df 取板块资金流)

    Returns:
        dict: {block_code, price_change, net_flow, divergence, reason}
        divergence ∈ {'top_divergence', 'bottom_divergence', None}
    """
    price_change = 0.0
    if context.pricevol_df is not None and not context.pricevol_df.empty:
        row = context.pricevol_df[context.pricevol_df['code'] == block_code]
        if not row.empty:
            r = row.iloc[0]
            price_change = _change_pct(r.get('Now'), r.get('LastClose'))

    net_flow = 0.0
    if context.sector_flow_df is not None and not context.sector_flow_df.empty:
        row = context.sector_flow_df[context.sector_flow_df['block_code'] == block_code]
        if not row.empty:
            net_flow = _safe_float(row.iloc[0].get('net_flow'))

    divergence = None
    if price_change > 1.0 and net_flow < 0:
        divergence = 'top_divergence'
        reason = f'顶背离: 板块涨{price_change:.2f}% 但资金净流出 {net_flow:.0f}'
    elif price_change < -1.0 and net_flow > 0:
        divergence = 'bottom_divergence'
        reason = f'底背离: 板块跌{price_change:.2f}% 但资金净流入 {net_flow:.0f}'
    else:
        reason = f'无背离: 板块{price_change:.2f}% 资金流 {net_flow:.0f}'

    return {
        'block_code': block_code,
        'price_change': round(price_change, 2),
        'net_flow': round(net_flow, 2),
        'divergence': divergence,
        'reason': reason,
    }
