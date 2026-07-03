"""大单监控 (快照差分)

脚本路径: K:\QuestDB_test\\strategy\\big_order.py
用途: 对比相邻两帧快照, 检测大额成交单 (大单/巨单/超大单), 判定主动方向
依赖: loguru
数据源: qd_stock_snapshot 相邻两帧 + more_info (Zjl 判方向)
阈值 (成交额, 元):
  BIG   = 100 万  (1_000_000)
  HUGE  = 500 万  (5_000_000)
  SUPER = 1000 万 (10_000_000)
方向判定:
  价格↑ (curr.Now > prev.Now) 且 Zjl > 0 → 主动买入
  价格↓ (curr.Now < prev.Now) 且 Zjl < 0 → 主动卖出
  其余 → 中性
说明:
  - detect 单只检测: amount_diff = curr.Amount - prev.Amount (累计成交额差分)
  - 达到 BIG 阈值返回事件 dict, 否则 None
  - 级别 big/huge/super 按 amount_diff 归档
"""

from loguru import logger

# 大单阈值 (元)
BIG_THRESHOLD = 1_000_000      # 100 万
HUGE_THRESHOLD = 5_000_000     # 500 万
SUPER_THRESHOLD = 10_000_000   # 1000 万


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _level(amount_diff) -> str:
    """按成交额差分归档级别"""
    if amount_diff >= SUPER_THRESHOLD:
        return 'super'
    if amount_diff >= HUGE_THRESHOLD:
        return 'huge'
    return 'big'


def _direction(curr_snap, prev_snap, zjl) -> str:
    """判定主动方向: buy / sell / neutral"""
    curr_now = _safe_float(curr_snap.get('Now'))
    prev_now = _safe_float(prev_snap.get('Now'))
    if curr_now > prev_now and zjl > 0:
        return 'buy'
    if curr_now < prev_now and zjl < 0:
        return 'sell'
    return 'neutral'


def detect(code, curr_snap, prev_snap, more_info):
    """检测单只大单事件

    Args:
        code: 股票代码
        curr_snap: 当前帧快照 dict (含 Amount/Now)
        prev_snap: 上一帧快照 dict (含 Amount/Now)
        more_info: more_info dict (含 Zjl 判方向)

    Returns:
        dict|None: 达到阈值返回
            {code, level, direction, amount_diff, price, zjl, reason}
            未达阈值或数据不足返回 None
    """
    if not curr_snap or not prev_snap:
        return None

    curr_amt = _safe_float(curr_snap.get('Amount'))
    prev_amt = _safe_float(prev_snap.get('Amount'))
    amount_diff = curr_amt - prev_amt

    if amount_diff < BIG_THRESHOLD:
        return None

    zjl = _safe_float((more_info or {}).get('Zjl'))
    level = _level(amount_diff)
    direction = _direction(curr_snap, prev_snap, zjl)
    price = _safe_float(curr_snap.get('Now'))

    level_cn = {'big': '大单', 'huge': '巨单', 'super': '超大单'}[level]
    dir_cn = {'buy': '主动买入', 'sell': '主动卖出', 'neutral': '中性'}[direction]
    reason = f'{level_cn}{dir_cn}: 成交额差分 {amount_diff / 1e4:.0f} 万, Zjl={zjl:.0f}'

    return {
        'code': code,
        'level': level,
        'direction': direction,
        'amount_diff': round(amount_diff, 2),
        'price': round(price, 4),
        'zjl': round(zjl, 2),
        'reason': reason,
    }


def detect_batch(code, frames, more_info_map=None) -> list:
    """批量检测单只股票多帧大单

    Args:
        code: 股票代码
        frames: list[dict] 按时间升序的快照帧 (含 snapshot_time/Amount/Now)
        more_info_map: dict {snapshot_time: more_info_dict} 可选, 缺省用 None

    Returns:
        list[dict]: 大单事件列表
    """
    events = []
    if not frames or len(frames) < 2:
        return events
    more_info_map = more_info_map or {}
    for i in range(1, len(frames)):
        prev = frames[i - 1]
        curr = frames[i]
        ts = curr.get('snapshot_time')
        mi = more_info_map.get(ts)
        ev = detect(code, curr, prev, mi)
        if ev:
            ev['time'] = ts
            events.append(ev)
    if events:
        logger.debug('大单检测 {}: {} 帧中 {} 个大单事件', code, len(frames), len(events))
    return events
