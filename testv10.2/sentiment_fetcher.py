"""testv10.2 大盘情绪采集层 (sentiment_fetcher) — 所有 tqcenter 调用集中于此

脚本路径: K:/QuestDB_test/testv10.2/sentiment_fetcher.py
职责: 纯采集, 返回标准化的 raw dict 供计算层 (sentiment_monitor) 消费。
      本模块不做任何算分/预警/写表 — 那是计算层的活。
分层 (CLAUDE.md import 方向): 采集层 → lib/config; 不反向依赖计算层。
数据源 (探针 2026-08-09 全绿):
  880001.SH snapshot  涨跌家数(UpHome/DownHome) + 涨跌停(Outside/Inside) + 成交额(Amount)
  880001.SH more_info 主力净流(Zjl_HB)
  880863.SH snapshot  昨日涨停指数(持续性)
  4 宽基 snapshot      风向 (加权涨幅)
  df (scan_universe)   涨幅分布 (亏钱效应) + 涨停候选预筛 (pct≥9.5)
  候选 more_info+snapshot  连板(EverZTCount)/封单(FCAmo)/封成比(FCb)/涨停价(ZTPrice)/今日高(Max)
依赖: lib.tq_client (safe_call), ticker (scan_universe), settings
"""

import bootstrap
bootstrap.ensure_paths()

import time  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402
import ticker as tk  # noqa: E402
import settings as cfg  # noqa: E402

_SNAP_FIELDS = ['Now', 'LastClose', 'Outside', 'Inside',
                'UpHome', 'DownHome', 'Amount', 'Zangsu']


def _f(v, default: float = 0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r  # NaN 兜底
    except (TypeError, ValueError):
        return default


def fetch_sentiment_raw(df) -> dict:
    """聚合大盘情绪全部原始数据 → 标准 raw dict (计算层唯一入参)。

    Args:
        df: funnel 的 scan_universe df (复用, 省一次 pricevol); None/空 则自取。
    Returns:
        dict: {market, amount_today, main_net, candidates, cand_drill_degraded, breadth, fetch_duration}
    """
    t0 = time.time()
    if df is None or len(df) == 0:
        df = tk.scan_universe()
    market = _fetch_market_snapshots()
    a = market.get(cfg.MARKET_ALL_INDEX, {})
    candidates, degraded = _drill_limit_candidates(df)
    return {
        'market': market,
        'amount_today': _f(a.get('Amount')),         # 万元 (880001 snapshot)
        'main_net': _fetch_main_net(),               # 万元 (880001 more_info Zjl_HB)
        'candidates': candidates,
        'cand_drill_degraded': degraded,
        'breadth': _breadth_from_df(df),             # 涨>5%/跌>5% 计数
        'fetch_duration': round(time.time() - t0, 2),
    }


def _fetch_market_snapshots() -> dict:
    """指数快照: 880001(全A) + 880863(昨涨停) + 4 宽基。单只接口, 无法批量。"""
    out: dict = {}
    codes = [cfg.MARKET_ALL_INDEX, cfg.YESTERDAY_ZT_INDEX] + list(cfg.BROAD_INDICES)
    for code in codes:
        try:
            out[code] = safe_call(tq.get_market_snapshot, stock_code=code,
                                  field_list=_SNAP_FIELDS) or {}
        except Exception as e:  # noqa: BLE001
            logger.debug('snapshot {} 失败: {}', code, e)
            out[code] = {}
    return out


def _fetch_main_net() -> float:
    """全A主力净流 (880001 more_info Zjl_HB, 万元)。失败返回 0。"""
    try:
        mi = safe_call(tq.get_more_info, stock_code=cfg.MARKET_ALL_INDEX, field_list=[]) or {}
        return _f(mi.get('Zjl_HB'))
    except Exception as e:  # noqa: BLE001
        logger.debug('主力净流取数失败: {}', e)
        return 0.0


# 日级留存: 进过候选池 (曾 pct≥9.5) 的 code 全天跟踪, 炸板跌出 9.5% 仍钻取
# (否则炸板股跌出样本 → 封板率被高估/炸板被低估, 钝化跳水预警)
_SEEN_CANDIDATES: set[str] = set()
_SEEN_DATE: str = ''


def _drill_limit_candidates(df) -> tuple[list, bool]:
    """df.pct≥9.5 候选 → more_info(EverZTCount/FCAmo/FCb/ZTPrice) + snapshot(Max)。

    日级留存: 当轮 pct≥9.5 的 code ∪ 今日曾进过候选的 code (炸板跌出仍跟踪)。
    Max+ZTPrice 用于真炸板判定 (Max≥ZTPrice 且 FCAmo≤0 = 曾封现开)。带预算超时 break。
    Returns: (candidates, degraded)。"""
    global _SEEN_CANDIDATES, _SEEN_DATE
    if df is None or len(df) == 0:
        return [], False
    # 跨日重置
    today = datetime.now().strftime('%Y-%m-%d')
    if _SEEN_DATE != today:
        _SEEN_CANDIDATES = set()
        _SEEN_DATE = today
    cand_df = df[df['pct'] >= cfg.NEAR_LIMIT_CAND_PCT].sort_values('pct', ascending=False)
    fresh_codes = cand_df['code'].tolist()[:200]
    _SEEN_CANDIDATES.update(fresh_codes)
    # 钻取 = 当轮新进 + 今日留存 (含炸板跌出者), 受预算 break
    cand_codes = [c for c in list(_SEEN_CANDIDATES)[:300]]
    out: list[dict] = []
    degraded = False
    t0 = time.time()
    for code in cand_codes:
        if time.time() - t0 > cfg.SENTIMENT_CAND_DRILL_BUDGET:
            degraded = True
            logger.warning('候选钻取超 {:.0f}s 预算, 已钻 {}/{}, 部分降级',
                           cfg.SENTIMENT_CAND_DRILL_BUDGET, len(out), len(cand_codes))
            break
        try:
            mi = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}
            sn = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[]) or {}
        except Exception:  # noqa: BLE001
            continue
        out.append({'code': code,
                    'EverZTCount': _f(mi.get('EverZTCount')),
                    'FCAmo': _f(mi.get('FCAmo')),
                    'FCb': _f(mi.get('FCb')),
                    'ZTPrice': _f(mi.get('ZTPrice')),
                    'Max': _f(sn.get('Max'))})
    return out, degraded


def _breadth_from_df(df) -> dict:
    """全A涨幅分布 → 涨>5%/跌>5% 家数 (亏钱效应)。df 来自 scan_universe (全A)。"""
    if df is None or len(df) == 0:
        return {'up5': 0, 'down5': 0}
    pcts = df['pct']
    return {'up5': int((pcts > 5).sum()), 'down5': int((pcts < -5).sum())}
