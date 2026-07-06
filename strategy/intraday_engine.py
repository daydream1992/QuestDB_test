"""intraday_engine: 盘中异动检测 (实盘订阅模块)

脚本路径: K:\QuestDB_test\\strategy\\intraday_engine.py
移植自 DB数据库_v2 01实盘监控/engine.py, 聚焦实用 4 类 (去粗取精, 见 ARCHITECTURE_REVIEW 批5):
  - surge_up/down: 5 分钟涨速 |Now/Before5MinNow - 1|*100 >= 2%
  - limit_seal:    封涨停 (现价 >= ZTPrice*0.999 且 卖一量 Sellv1 <= 100 手)
  - limit_break:   炸板 [critical] (封板后跌离涨停价)
  - capital_in/out: 主力 Zjl 流入/流出 >= 2000 万

去粗取精 (Agent 3 建议, 本次不实现):
  - 趋势反转 (15s 均线噪声)、超买超卖 (非标 RSI)、量能放大 (信息低)、涨跌幅触及 (噪声)
  - dark_flow 明暗盘 (cold_start 30 分钟, 降级 TODO)

数据源: ctx.snapshot_focus_df (qd_stock_snapshot, c2 快照 + c3 intraday, 合并双形态行)
状态: MonitorState 跨轮 (模块级 dict, intraday_loop daemon 常驻)
推送: critical (炸板) 立即飞书; 其余 Deduper 180s 频控; 入库 qd_intraday_event
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import executemany_batch, cutoff  # noqa: E402
import importlib as _il
_feishu = _il.import_module('feishu')  # noqa: E402
from lib.limit_rule import is_at_limit_up  # noqa: E402
from lib.notify_dedup import allow_push  # noqa: E402
from lib.relation_graph import get_stock_name  # noqa: E402

DST = 'qd_intraday_event'
_EVENT_COLS = ['event_time', 'code', 'event_type', 'description', 'critical']

# 阈值 (集中, 调阈值不改逻辑)
SURGE_PCT = 1.0           # 5 分钟涨速绝对值 >= 1%
LIMIT_SELLV_MAX = 500     # 卖一量 <= 500 手 视为封死 (卖一被吃光)
CAPITAL_FLOW_MIN = 5e6    # 主力 |Zjl| >= 500 万


@dataclass
class MonitorState:
    """每 code 跨轮状态"""
    last_limit_sealed: bool = False


# 模块级状态 (daemon 常驻, 每 code 一个; 跨轮保留封板状态)
_STATES: dict = {}


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def detect_surge(now, before5min):
    """5 分钟涨速异动"""
    if before5min <= 0:
        return None
    chg = (now / before5min - 1) * 100
    if chg >= SURGE_PCT:
        return ('surge_up', f'5分钟急涨{chg:.2f}%', False)
    if chg <= -SURGE_PCT:
        return ('surge_down', f'5分钟急跌{chg:.2f}%', False)
    return None


def detect_limit(state: MonitorState, fcamo):
    """封板/炸板 (FCAmo 权威判定: >0 真封板有封单, <=0 未封; 炸板 critical)

    替代旧 is_at_limit_up(now,zt_price)+sellv1 逻辑 (Now>=ZTPrice 会误判触价未封的假涨停)。
    """
    sealed = fcamo > 0
    if sealed and not state.last_limit_sealed:
        state.last_limit_sealed = True
        return ('limit_seal', '封涨停', False)
    if not sealed and state.last_limit_sealed:
        state.last_limit_sealed = False
        return ('limit_break', '炸板', True)  # critical: 绝不被频控去重
    return None


def detect_capital(zjl):
    """主力资金异动"""
    if zjl >= CAPITAL_FLOW_MIN:
        return ('capital_in', f'主力流入{zjl / 1e4:.0f}万', False)
    if zjl <= -CAPITAL_FLOW_MIN:
        return ('capital_out', f'主力流出{zjl / 1e4:.0f}万', False)
    return None


def detect_all(snapshot_df, watchlist=None):
    """对 snapshot_focus (合并双形态行) 跑异动检测

    Args:
        snapshot_df: qd_stock_snapshot DataFrame (重点池, 含 c2+c3 字段)
        watchlist: 订阅池代码列表 (预留; 当前重点池已覆盖, 后续可强制纳入)

    Returns:
        list[(code, event_type, desc, critical)]
    """
    events = []
    # C8 拆表后: snapshot_df 已由调用方 merge 完整 (快照+intraday), 取每 code 最新一行
    if snapshot_df is None or snapshot_df.empty:
        return events
    if 'snapshot_time' in snapshot_df.columns:
        merged = snapshot_df.sort_values('snapshot_time').groupby('code', as_index=False).last()
    else:
        merged = snapshot_df
    if merged is None or merged.empty:
        return events
    for _, r in merged.iterrows():
        code = r.get('code')
        if not code:
            continue
        st = _STATES.setdefault(code, MonitorState())
        now = _safe_float(r.get('Now'))
        before5min = _safe_float(r.get('Before5MinNow'))
        zt_price = _safe_float(r.get('ZTPrice'))
        sellv1 = _safe_float(r.get('Sellv1'))
        zjl = _safe_float(r.get('Zjl'))
        fcamo = _safe_float(r.get('FCAmo'))
        for res in (detect_surge(now, before5min),
                    detect_limit(st, fcamo),
                    detect_capital(zjl)):
            if res:
                events.append((code, *res))
    return events


def run(con, snapshot_df, watchlist=None):
    """主流程: 检测 → 写 qd_intraday_event + critical 即时飞书 (Deduper 频控)

    Args:
        con: psycopg2 连接
        snapshot_df: ctx.snapshot_focus_df
        watchlist: 订阅池 (预留)

    Returns:
        int: 检测到的事件数
    """
    events = detect_all(snapshot_df, watchlist)
    if not events:
        return 0
    now = datetime.now()
    rows = []
    pushed = []
    for code, etype, desc, critical in events:
        rows.append((now, code, etype, desc, bool(critical)))
        # critical 立即推; 其余 Deduper 频控 (180s 同 code+type 不重复)
        if allow_push(code, etype, critical=critical):
            pushed.append((code, etype, desc, critical))
    if rows:
        try:
            executemany_batch(con, DST, _EVENT_COLS, rows)
        except Exception as e:
            logger.warning('写 qd_intraday_event 失败: {}', e)
    if pushed:
        try:
            feishu_signals = []
            for code, etype, desc, critical in pushed:
                feishu_signals.append({
                    'code': code,
                    'stock_name': get_stock_name(code),
                    'strategy_name': 'intraday_engine',
                    'action': etype,
                    'reason': desc,
                    'decision_time': now,
                    'price': None,
                    'position_size': 0,
                })
            # 异动只推+写表格, 不再单独 push_text (log_signals 内含推送)
            _feishu.log_signals(feishu_signals, sheet=True, bitable=True)
        except Exception as e:
            logger.warning('异动飞书写入失败: {}', e)
    logger.info('intraday_engine: 检测 {} 事件, 推送 {}', len(events), len(pushed))
    return len(events)


if __name__ == '__main__':
    # 独立运行: 读快照+intraday 两表, 按 code merge 测试 (C8 拆表后)
    from lib.qdb import connect
    from lib.qdb import query_df
    con = connect()
    try:
        snap = query_df(con, "SELECT * FROM qd_stock_snapshot "
                            f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        intra = query_df(con, "SELECT * FROM qd_stock_intraday "
                         f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        # 按 code 取最新 intraday merge 进快照
        if intra is not None and not intra.empty:
            il = intra.sort_values('snapshot_time').groupby('code', as_index=False).tail(1)
            ic = [c for c in il.columns if c not in ('code', 'snapshot_time')]
            snap = snap.drop(columns=[c for c in ic if c in snap.columns])
            df = snap.merge(il[['code'] + ic], on='code', how='left')
        else:
            df = snap
        n = run(con, df)
        print(f'检测到 {n} 个事件')
    finally:
        con.close()
