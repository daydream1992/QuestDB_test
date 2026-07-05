"""auction_engine: 竞价规则树标签 + 线性拟合

脚本路径: K:\QuestDB_test\\strategy\\auction_engine.py
移植自 DB数据库_v2 竞价监控/engine.py v2 规则树 + 线性拟合。
核心:
  - zjl_ratio: 资金强度相对化 = Zjl / 流通市值 (跨市值可比, 比 Zjl 绝对值科学)
  - label_row: 规则树标签 (优先级从严到宽)
      高开: trap_warning(惯骗否决) > fund_diverge(主力流出) > strong_continue(强势延续)
      低开: nuclear(核按钮, 昨涨停今低开) > dip_buy(低吸)
  - fit_trend: 竞价 6 点价格序列线性拟合 → (slope_pct, r2)

说明:
  - preset 预备包模式 (盘前落 T-1 特征, 9:25 仅取开盘价) 由 auction_monitor 实现
  - 骗炮 trap_cnt 来自 qd_pianpao_daily (k4_pianpao 盘后产出)
  - 流通市值用 qd_stock_daily.FreeLtgb × 开盘价 (已有字段, 无需新建采集)
"""

from typing import Tuple


def calc_zjl_ratio(zjl: float, price: float, float_shares: float) -> float:
    """资金强度 = 主力净流入 / 流通市值 (万分比)

    跨市值可比: 2000 万对茅台和小票含义不同, 用相对市值比例统一。
    """
    mcap = price * float_shares
    if mcap <= 0:
        return 0.0
    return zjl * 1e4 / mcap


def label_row(open_pct: float, prev_zt: bool, zjl_ratio: float,
              trap_cnt: int, float_mcap_yi: float = 0.0) -> Tuple[str, str]:
    """规则树标签 (优先级从严到宽)

    Args:
        open_pct: 开盘涨幅 % ((open - prev_close) / prev_close * 100)
        prev_zt: 昨日是否涨停
        zjl_ratio: 资金强度 (万分比, calc_zjl_ratio)
        trap_cnt: 近 60 天骗炮次数 (qd_pianpao_daily)
        float_mcap_yi: 流通市值 (亿元, 辅助流动性标签)

    Returns:
        (label, reason)
        label ∈ {trap_warning, fund_diverge, strong_continue, nuclear, dip_buy,
                 liquidity, neutral}
    """
    # 流动性警示 (叠加, 流通市值 < 30 亿)
    liquidity_tag = ' ⚠流动性' if 0 < float_mcap_yi < 30 else ''

    if open_pct > 1.0:  # 高开
        if trap_cnt >= 1:
            return ('trap_warning', f'高开{open_pct:.1f}%+惯炮{trap_cnt}次, 一票否决{liquidity_tag}')
        if zjl_ratio < -0.5:
            return ('fund_diverge', f'高开{open_pct:.1f}%但主力流出{zjl_ratio:.2f}‰{liquidity_tag}')
        if prev_zt and zjl_ratio > 0.1:
            return ('strong_continue', f'昨涨停+高开{open_pct:.1f}%+主力流入{zjl_ratio:.2f}‰{liquidity_tag}')
        return ('neutral', f'高开{open_pct:.1f}%中性{liquidity_tag}')

    if open_pct < -1.0:  # 低开
        if prev_zt:
            return ('nuclear', f'昨涨停今低开{open_pct:.1f}%(核按钮){liquidity_tag}')
        if -3.0 <= open_pct < -1.0 and zjl_ratio >= -0.5 and trap_cnt < 1:
            return ('dip_buy', f'小低开{open_pct:.1f}%+主力未流出, 低吸观察{liquidity_tag}')
        return ('neutral', f'低开{open_pct:.1f}%中性{liquidity_tag}')

    return ('neutral', f'平开{liquidity_tag}')


def fit_trend(prices) -> Tuple[float, float]:
    """竞价价格序列线性拟合 (纯 python, 不依赖 numpy)

    Args:
        prices: 6 点价格列表 (9:15-9:25 每 2 分钟)

    Returns:
        (slope_pct, r2): 斜率 (相对首价的 %) + 决定系数 (稳定性)
    """
    n = len(prices)
    if n < 2:
        return (0.0, 0.0)
    xs = list(range(n))
    ys = [float(p) for p in prices]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den != 0 else 0.0
    # r2
    ss_res = sum((y - (slope * (x - mx) + my)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    r2 = max(0.0, min(1.0, r2))
    slope_pct = (slope / ys[0] * 100) if ys[0] != 0 else 0.0
    return (slope_pct, r2)


def interpret_trend(slope_pct: float, r2: float) -> str:
    """解读竞价趋势 (配合 fit_trend)"""
    if slope_pct < 0.05:
        return '弱/下行'
    if r2 >= 0.7:
        return f'稳定上升 {slope_pct:.2f}%'
    if r2 >= 0.3:
        return f'波动上升 {slope_pct:.2f}% (r2={r2:.2f})'
    return f'凌乱 {slope_pct:.2f}% (r2={r2:.2f})'
