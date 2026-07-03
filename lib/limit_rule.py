"""涨停/跌停规则 (代码前缀 → 涨跌幅)

移植自 DB数据库_v2 (01实盘监控 + 00大盘情绪 各有一份, Q 统一一份避免重复)。
规则:
  - 科创板 (688/689) / 创业板 (300): ±20%
  - 北交所 (920/83/87/43): ±30%
  - ST: ±5% (需调用方传 st 标志; 此处不自动判 ST)
  - 其余主板/中小板: ±10%
"""

from typing import Optional


def _prefix(code: str) -> str:
    return (code or '').split('.')[0]


def limit_up_pct(code: str, st: bool = False) -> float:
    """涨停幅度 %"""
    if st:
        return 5.0
    p = _prefix(code)
    if p.startswith(('688', '300', '689')):
        return 20.0
    if p.startswith(('920', '83', '87', '43')):  # 北交所
        return 30.0
    return 10.0


def limit_down_pct(code: str, st: bool = False) -> float:
    """跌停幅度 % (负值)"""
    return -limit_up_pct(code, st)


def is_at_limit_up(now, zt_price, ratio: float = 0.999) -> bool:
    """现价 >= 涨停价 * ratio 视为触及涨停 (ZTPrice 字段优先)"""
    try:
        now = float(now)
        zt = float(zt_price)
    except (TypeError, ValueError):
        return False
    return zt > 0 and now >= zt * ratio


def calc_zt_price(last_close, code: str, st: bool = False) -> float:
    """无 ZTPrice 字段时, 按前缀规则算涨停价"""
    try:
        lc = float(last_close)
    except (TypeError, ValueError):
        return 0.0
    return lc * (1 + limit_up_pct(code, st) / 100)


def classify(now, last_close, code: str,
             zt_price: Optional[float] = None,
             dt_price: Optional[float] = None,
             st: bool = False) -> str:
    """判定涨跌停状态: 'zt' / 'dt' / '' """
    zt = zt_price if zt_price else calc_zt_price(last_close, code, st)
    if is_at_limit_up(now, zt):
        return 'zt'
    try:
        now_f = float(now)
        lc = float(last_close)
    except (TypeError, ValueError):
        return ''
    if lc <= 0:
        return ''
    dt = dt_price if dt_price else lc * (1 + limit_down_pct(code, st) / 100)
    if now_f <= dt * 1.001:
        return 'dt'
    return ''
