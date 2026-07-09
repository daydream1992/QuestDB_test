"""量价背离检测

检测价格与成交量的背离：
- 顶背离：价格创新高，但成交量萎缩 → 上涨乏力，可能回落
- 底背离：价格创新低，但成交量萎缩（抛压减少）→ 下跌乏力，可能反弹

原理：
- 用滚动窗口计算价格和成交量的变化趋势
- 趋势方向相反即为背离
"""

import pandas as pd
from loguru import logger

# 背离参数
_WINDOW = 10          # 滚动窗口大小
_PRICE_WEIGHT = 0.6  # 价格权重
_VOLUME_WEIGHT = 0.4  # 成交量权重


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def detect_divergence(df, code=None) -> dict:
    """检测量价背离

    Args:
        df: DataFrame 含 code/Now/Volume/snapshot_time 列
        code: 可选，指定检测某只股票

    Returns:
        dict: {code, direction, price_change, volume_change, signal, reason}
            direction: 'top' 顶背离 / 'bottom' 底背离 / None
    """
    if df is None or df.empty:
        return {}

    # 过滤指定股票
    if code:
        df = df[df.get('code') == code]

    if df.empty:
        return {}

    # 按时间排序
    if 'snapshot_time' in df.columns:
        df = df.sort_values('snapshot_time').tail(_WINDOW).copy()
    else:
        df = df.tail(_WINDOW).copy()

    if len(df) < 3:
        return {}

    # 计算价格和成交量的相对变化
    first_row = df.iloc[0]
    last_row = df.iloc[-1]

    price_first = _safe_float(first_row.get('Now'))
    price_last = _safe_float(last_row.get('Now'))
    vol_first = _safe_float(first_row.get('Volume'))
    vol_last = _safe_float(last_row.get('Volume'))

    # 计算变化率（用相对比例，避免绝对值差异）
    price_change = (price_last - price_first) / price_first if price_first > 0 else 0
    vol_change = (vol_last - vol_first) / vol_first if vol_first > 0 else 0

    # 判断背离
    signal = None
    direction = None
    reason = None

    # 顶背离：价格涨 + 成交量萎缩
    if price_change > 0.01 and vol_change < -0.05:  # 价格涨1%+, 成交量萎缩5%+
        direction = 'top'
        signal = '顶背离预警'
        reason = f'价涨{price_change*100:.1f}%量缩{abs(vol_change)*100:.1f}%，上涨乏力'

    # 底背离：价格跌 + 成交量萎缩（抛压减少）
    elif price_change < -0.01 and vol_change < -0.05:  # 价格跌1%+ 且成交量萎缩5%+
        direction = 'bottom'
        signal = '底背离信号'
        reason = f'价跌{abs(price_change)*100:.1f}%量缩{abs(vol_change)*100:.1f}%，抛压减少'

    return {
        'code': code or (first_row.get('code') if 'code' in df.columns else None),
        'direction': direction,
        'price_change': round(price_change * 100, 2),
        'volume_change': round(vol_change * 100, 2),
        'signal': signal,
        'reason': reason,
    }


def detect_all(df, threshold=0.01) -> list:
    """检测所有股票的量价背离

    Args:
        df: DataFrame 含 code/Now/Volume/snapshot_time 列
        threshold: 价格变化阈值（默认1%）

    Returns:
        list[dict]: 背离信号列表
    """
    if df is None or df.empty:
        return []

    if 'code' not in df.columns:
        return []

    results = []
    for code in df['code'].unique():
        result = detect_divergence(df, code)
        if result.get('signal'):
            results.append(result)

    if results:
        logger.debug('量价背离检测: {} 只触发', len(results))

    return results
