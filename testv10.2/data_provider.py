"""testv10.2 统一采集层 (data_provider) — 所有计算模块通过此取 raw bundle

脚本路径: K:/QuestDB_test/testv10.2/data_provider.py
职责: 按 stage 统一采集 raw bundle, 供计算层 (并联) 共用。一次采集, 全模块复用。
接口规范 (已核实说明书, 严守):
  - 实时行情统一 get_market_snapshot (禁 get_full_tick, 无正式文档)
  - 板块成分股 get_stock_list_in_sector(block_code=...) (非 sector_name; 不支持全A, 用 mapping_store)
  - 不在本层调 send_warn/subscribe_hq (那是 alert/订阅层职责)
分层: 采集层 → lib/config/sentiment_fetcher/ticker; 不反向依赖计算层。
Phase1: bundle 先包 sentiment_fetcher (盘中情绪); 后续 phase 扩竞价(OpenAmo)/开盘(snapshot Open)/尾盘/收盘采集。
"""

import bootstrap
bootstrap.ensure_paths()

import time  # noqa: E402

from loguru import logger  # noqa: E402

import ticker as tk  # noqa: E402
import sentiment_fetcher as fetcher  # noqa: E402
import settings as cfg  # noqa: E402
from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402


def _f(v, default: float = 0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r
    except (TypeError, ValueError):
        return default


def fetch_auction_raw() -> dict:
    """竞价采集 (9:15-9:25): 全板块 Outside竞价涨停家数 + OpenAmo竞价金额;
    竞价涨停板块(Outside>0)成分股 OpenZTBuy>0 → 一字候选 (给 open_monitor)。
    接口: get_stock_list(market) + get_market_snapshot + get_more_info + get_stock_list_in_sector(block_code)。"""
    t0 = time.time()
    boards = safe_call(tq.get_stock_list, market=cfg.AUCTION_BOARD_MARKET, list_type=0) or []
    board_auction: list[dict] = []
    zt_boards: list[str] = []
    for bc in boards:
        if time.time() - t0 > cfg.AUCTION_BOARD_BUDGET:
            break
        sn = safe_call(tq.get_market_snapshot, stock_code=bc, field_list=['Outside']) or {}
        mi = safe_call(tq.get_more_info, stock_code=bc, field_list=[]) or {}
        outside = _f(sn.get('Outside'))
        open_amo = _f(mi.get('OpenAmo'))       # 元 (DYNAINFO(15); 文档误标万元)
        pre1 = _f(mi.get('OpenAmoPre1'))       # 昨开盘金额 万元
        # 竞价昨量比: OpenAmo(元)/1e4→万 ÷ OpenAmoPre1(万), 无量纲
        ratio = ((open_amo / 1e4) / pre1) if pre1 > 0 else 0.0
        board_auction.append({'code': bc, 'outside': int(outside),
                              'open_amo': open_amo, 'ratio': round(ratio, 2)})
        if outside > 0:
            zt_boards.append(bc)
    # 一字候选: 竞价涨停板块成分股 OpenZTBuy>0
    candidates: list[dict] = []
    for bc in zt_boards[:cfg.AUCTION_MAX_ZT_BOARDS]:
        if len(candidates) >= cfg.AUCTION_MAX_CANDIDATES:
            break
        members = safe_call(tq.get_stock_list_in_sector, block_code=bc) or []
        for sc in members:
            if len(candidates) >= cfg.AUCTION_MAX_CANDIDATES:
                break
            mi = safe_call(tq.get_more_info, stock_code=sc, field_list=[]) or {}
            openzt = _f(mi.get('OpenZTBuy'))
            if openzt > cfg.AUCTION_OPENZT_MIN:
                candidates.append({'code': sc, 'openzt': openzt,
                                   'lb': int(_f(mi.get('EverZTCount')))})
    candidates.sort(key=lambda x: x['openzt'], reverse=True)
    # 放量板块榜 (按竞价昨量比降序 — 放量=相对昨日放量, 比绝对金额更准)
    top_boards = sorted(board_auction, key=lambda b: b['ratio'], reverse=True)[:10]
    return {'top_boards': top_boards, 'zt_board_n': len(zt_boards),
            'candidates': candidates, 'duration': round(time.time() - t0, 1)}


def fetch_bundle(stage: str, df_hint=None) -> dict:
    """统一采集 → raw bundle (计算层唯一数据来源)。

    Args:
        stage: 当前交易时段 (auction/open/intraday/tail/close/off), 决定采哪些数据
        df_hint: funnel 已取的全A df (复用, 省 pricevol); None/空则本层自取
    Returns:
        bundle dict: {stage, df, sentiment_raw, ...后续 phase 扩}
    """
    df = df_hint if (df_hint is not None and len(df_hint) > 0) else tk.scan_universe()
    bundle = {'stage': stage, 'df': df}
    # 盘中/尾盘/收盘: 大盘情绪 + 候选钻取 (sentiment_fetcher 复用, 含 market/amount/main_net/candidates/breadth)
    if stage in ('intraday', 'tail', 'close', 'off'):
        try:
            bundle['sentiment_raw'] = fetcher.fetch_sentiment_raw(df)
        except Exception:  # noqa: BLE001  故障隔离: 情绪采集失败降级, 不拖垮整轮
            logger.exception('sentiment 采集失败, 降级为空 (本轮情绪/候选缺失)')
            bundle['sentiment_raw'] = None
    # 竞价: 板块竞价榜 + 一字候选 (给 open_monitor)
    if stage == 'auction':
        try:
            bundle['auction_raw'] = fetch_auction_raw()
        except Exception:  # noqa: BLE001  故障隔离: 竞价采集失败降级, 不拖垮整轮
            logger.exception('auction 采集失败, 降级为空 (本轮竞价榜/候选缺失)')
            bundle['auction_raw'] = None
    return bundle
