"""testv10.2 宽带雷达主循环 (radar_main) — 单进程 asyncio-free 版

脚本路径: K:/QuestDB_test/testv10.2/radar_main.py
用途: 1 分钟/轮串联 funnel: 板块扫描(meso) → 状态机入池(board_pool) → 全场预筛(ticker)
      → lean 钻取(每板 TopN) → 控制台摘要。信号萃取/Excel/飞书留接口暂不实现。
依赖: lib/tq_client, mapping_store, meso_radar, board_pool, ticker, settings
架构: 单进程 (实测 meso~7s+pricevol~2.4s+lean钻取~5s ≈ 15s 盘前, 盘中宽裕; 秒级 tick 留接口)
跑法:
  python testv10.2/radar_main.py --force --rounds 1   # 盘前/验证 (跳门控, 跑 N 轮退出)
  python testv10.2/radar_main.py                       # 生产 (交易时段门控, 循环到收盘)
"""

import bootstrap
bootstrap.ensure_paths()

import argparse
import os  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

from lib.tq_client import init, close  # noqa: E402
from lib import market_clock as tc  # noqa: E402
import settings as cfg  # noqa: E402
import mapping_store as ms_mod  # noqa: E402
from meso_radar import MesoRadar, pool_boards  # noqa: E402
from board_pool import BoardPool  # noqa: E402
import ticker as tk  # noqa: E402
import publisher as pub_mod  # noqa: E402
from sentiment_monitor import SentimentMonitor  # noqa: E402
from rotation import RotationMonitor  # noqa: E402
from open_monitor import OpenMonitor  # noqa: E402
from auction_monitor import AuctionMonitor  # noqa: E402
from tail_monitor import TailMonitor  # noqa: E402
from stock_ranking import StockRanking  # noqa: E402
from blindspot_monitor import BlindspotMonitor  # noqa: E402
from alert_engine import AlertEngine  # noqa: E402
import data_provider  # noqa: E402  统一采集层 (bundle)

ROUND_INTERVAL = 60  # 秒 (雷达 1 分钟/轮)

# 文件日志 (盘中长跑可追溯)
os.makedirs(cfg.LOG_DIR, exist_ok=True)
logger.add(os.path.join(cfg.LOG_DIR, 'testv10.2_radar_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')
# 池内 行业:概念 比例 CSV (连续观察, 判定混排是否打架; 见 memory: 行业分层观察)
POOL_RATIO_CSV = os.path.join(cfg.LOG_DIR, 'pool_ratio.csv')


def _append_pool_ratio(now: datetime, pool_lv: dict, total: int) -> None:
    """每轮写一行池内层级比例 (行业:概念), 供多日观察。轻量, 失败不崩。"""
    try:
        ind = pool_lv.get('三级', 0)
        con = pool_lv.get('概念', 0)
        ratio = (ind / total) if total else 0.0
        with open(POOL_RATIO_CSV, 'a', encoding='utf-8') as f:
            if f.tell() == 0:
                f.write('时间,池大小,行业(三级),概念,行业占比\n')
            f.write(f'{now.strftime("%Y-%m-%d %H:%M:%S")},{total},{ind},{con},'
                    f'{ratio:.2f}\n')
    except Exception:  # noqa: BLE001  记录失败不影响雷达
        pass


def select_drill_candidates(hot_codes: list[str], ms, pct_map: dict[str, float],
                            zt_map: dict[str, float] | None = None) -> list[str]:
    """每 HOT/NEW 板取成分股, 按 pct 降序取 TopN (热点板涨停≥10 动态扩 Top15),
    全局去重保序。"""
    candidates: list[str] = []
    zt_map = zt_map or {}
    for bc in hot_codes:
        members = ms.stocks_of(bc)
        ranked = sorted(((c, pct_map.get(c, 0.0)) for c in members if c in pct_map),
                        key=lambda x: x[1], reverse=True)
        top_n = (cfg.DRILL_TOP_PER_HOT_BOARD if zt_map.get(bc, 0) >= cfg.DRILL_HOT_ZT_THRESH
                 else cfg.DRILL_TOP_PER_BOARD)
        for c, _ in ranked[:top_n]:
            if c not in candidates:
                candidates.append(c)
    return candidates


def run_one_round(round_idx: int, ms, radar: MesoRadar, pool: BoardPool,
                  sentiment, rotations: list, auction_monitor, tail_monitor,
                  stock_ranking, alert_engine, blindspot, is_close: bool,
                  now: datetime) -> dict:
    """跑一轮 funnel + 计算层并联 (per-module try 故障隔离), 返回摘要 dict。"""
    t0 = time.time()
    rows = radar.scan()
    # 动态池容量 (按探照灯命中数: 平淡日25 / 正常30 / 活跃35; 只放大不缩小保稳定)
    pool.adjust_cap(sum(1 for r in rows if r.get('searchlights')))
    entered = pool.update(pool_boards(rows), round_idx)
    new_entries = [c for c in entered if c in pool.boards]   # 只保留未被 hard_cap 裁掉的
    hot = pool.hot_codes()

    drilled: dict = {}
    df = None
    if hot:
        df = tk.scan_universe()
        pct_map = dict(zip(df['code'], df['pct']))
        zt_map = {r['code']: r.get('ZTGPNum', 0) for r in rows}   # 热点板涨停数 (动态扩Top)
        candidates = select_drill_candidates(hot, ms, pct_map, zt_map)
        drilled = tk.drill_stocks(candidates, df=df) if candidates else {}

    # === 分时段统一采集 + 计算层并联 (per-module try 故障隔离) ===
    stage = cfg.get_stage(now)
    bundle = data_provider.fetch_bundle(stage, df)
    # 大盘情绪 (close 段 force_push 收盘定格绕门控; 否则 maybe_push)
    if sentiment:
        try:
            if is_close:
                sentiment.force_push(bundle, rows, now)
            else:
                sentiment.maybe_push(bundle, rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('sentiment 模块异常, 跳过 (故障隔离)')
    # 板块轮动 (close 定格 / 盘中+tail maybe_push)
    if is_close or stage in ('intraday', 'tail'):
        for r in rotations:
            try:
                if is_close:
                    r.force_push(rows, now)
                else:
                    r.maybe_push(rows, now)
            except Exception:  # noqa: BLE001
                logger.exception('{}轮动异常, 跳过 (故障隔离)', r.label)
    # 竞价 (auction 段; 产出放量板块榜 + 一字候选→open_monitor)
    if stage == 'auction' and auction_monitor:
        try:
            auction_monitor.maybe_push(bundle, now)
        except Exception:  # noqa: BLE001
            logger.exception('auction 模块异常, 跳过 (故障隔离)')
    # 尾盘炸板风险 (tail 段)
    if stage == 'tail' and tail_monitor:
        try:
            tail_monitor.maybe_push(bundle, now)
        except Exception:  # noqa: BLE001
            logger.exception('tail 模块异常, 跳过 (故障隔离)')
    # 个股排名 (盘中/tail; 打板选股底座)
    if stage in ('intraday', 'tail') and stock_ranking:
        try:
            stock_ranking.maybe_push(drilled, now)
        except Exception:  # noqa: BLE001
            logger.exception('stock_ranking 异常, 跳过 (故障隔离)')
    # 盲区补盲 Top20 提取 (盘中/tail; 按个股榜分降序, 供 blindspot 订阅感知 60s 盲区)
    if stage in ('intraday', 'tail') and blindspot:
        try:
            _k1, _w1, _k2, _w2 = cfg.BLINDSPOT_SORT
            top20 = [c for c, _ in sorted(drilled.items(),
                                          key=lambda kv: kv[1].get(_k1, 0) * _w1
                                          + kv[1].get(_k2, 0) * _w2,
                                          reverse=True)[:cfg.BLINDSPOT_TOPN]]
            blindspot.refresh(top20)
            blindspot.process_pending()   # 盲区回调消化 (串行, 回调不碰 COM)
        except Exception:  # noqa: BLE001
            logger.exception('盲区补盲异常, 跳过 (故障隔离)')
    # 统一预警引擎 (读各模块 last_result; 盘中/tail/close)
    if stage in ('intraday', 'tail', 'close') and alert_engine:
        try:
            results = {'sentiment': sentiment.last_result if sentiment else None,
                       'tail': tail_monitor.last_result if tail_monitor else None}
            alert_engine.check(results, pool, drilled, now, stage)
        except Exception:  # noqa: BLE001
            logger.exception('alert_engine 异常, 跳过 (故障隔离)')

    leaders = sorted(drilled.items(), key=lambda kv: kv[1]['ZAF'], reverse=True)[:10]
    # 池内 行业(三级):概念 比例 (观察层级信号; 若行业稳定50-70% 则混排无打架)
    lv_map = {r['code']: r['level'] for r in rows}
    from collections import Counter
    pool_lv = Counter(lv_map.get(c, '?') for c in pool.boards)
    pool_ratio = f"池内 {pool_lv.get('三级',0)}行业/{pool_lv.get('概念',0)}概念"
    logger.info('轮 {}: 池 {} (NEW {}/HOT {}) | {} | 新入池 {} | 钻取 {} 股 | 耗时 {:.1f}s',
                round_idx, len(pool.boards), len(new_entries),
                sum(1 for s in pool.boards.values() if s.state == 'HOT'),
                pool_ratio, new_entries[:5], len(drilled), time.time() - t0)
    _append_pool_ratio(now, pool_lv, len(pool.boards))
    return {'round': round_idx, 'new_entries': new_entries, 'hot': hot,
            'pool_stats': pool.stats(), 'leaders': leaders}


def _print_summary(res: dict, ms) -> None:
    print(f"\n===== 轮 {res['round']} | {res['pool_stats']} =====")
    if res['new_entries']:
        print(f'🟢 新入池 (起涨): {res["new_entries"][:8]}')
    print(f'HOT/NEW 板块 ({len(res["hot"])}): ' + ', '.join(
        f'{c}({ms.board_name(c)[:4]})' for c in res['hot'][:10]))
    if res['leaders']:
        print('Leader Top 8 (按 ZAF):')
        for c, d in res['leaders'][:8]:
            print(f'  [{c}] {ms.stock_name(c)[:6]:<7} ZAF={d["ZAF"]:>6.2f} '
                  f'FCAmo={d["FCAmo"]:>8.0f} fLianB={d["fLianB"]:>5.2f} pos={d["pos_ratio"]:.2f}')


def run(rounds: int | None = None, force: bool = False, push: bool = False) -> None:
    ms = ms_mod.MappingStore(cfg.MAPPING_PARQUET)
    radar = MesoRadar(ms, cfg.MONITOR_LEVELS)
    pool = BoardPool()
    pub = pub_mod.Publisher(dry_run=not push)
    sentiment = SentimentMonitor(ms, pub, dry_run=not push)   # --push 真写飞书+真发警报, 否则 dry-run
    rotations = [RotationMonitor(lvls, label, table_base, dry_run=not push)
                 for label, table_base, lvls in cfg.ROTATION_LEVELS.values()]
    open_mon = OpenMonitor(ms, pub, dry_run=not push)
    auction_mon = AuctionMonitor(dry_run=not push)
    tail_mon = TailMonitor(ms, dry_run=not push)
    stock_ranking = StockRanking(ms, dry_run=not push)
    alert_engine = AlertEngine(ms, pub, dry_run=not push)
    blindspot = BlindspotMonitor(ms, dry_run=not push)
    init()
    round_idx = 0
    last_poll: datetime | None = None   # 开盘段 run_one_round 降频计时
    close_done = False                   # 收盘定格只跑一次
    logger.info('===== radar_main 启动 (rounds={} force={} push={}) =====', rounds, force, push)
    try:
        while True:
            now = datetime.now()
            # 退出: 跑完指定轮 / 15:00 收盘
            if rounds is not None and round_idx >= rounds:
                logger.info('跑完 {} 轮, 退出', rounds); break
            if rounds is None and now.time() >= cfg.TRADING_CLOSE:
                logger.info('15:00 收盘, 退出'); break
            # 非交易门控 (生产; force 跳过)
            if not force and rounds is None:
                if not tc.is_trading_day(now):
                    logger.info('非交易日, 退出'); break
                if not tc.is_trading_time(now):
                    if now.minute % 5 == 0 and now.second < 10:
                        logger.info('非交易时段, 等待 (现在 {})', now.strftime('%H:%M'))
                    time.sleep(10); continue

            stage = cfg.get_stage(now)
            # 开盘订阅管理 (进 open 启动, 离 open 停止); 候选优先用竞价产出
            if stage == 'open' and not open_mon.started:
                cands = auction_mon.last_candidates or cfg.OPEN_WATCHLIST
                open_mon.start(cands)
            elif stage != 'open' and open_mon.started:
                open_mon.stop()
            # 盲区补盲订阅 (盘中/tail 启动, 其他时段停止); refresh 在 run_one_round 内
            if stage in ('intraday', 'tail') and not blindspot.started:
                blindspot.started = True
                logger.info('盲区补盲: 进入盘中, 待首轮钻取后订阅 Top{}', cfg.BLINDSPOT_TOPN)
            elif stage not in ('intraday', 'tail') and blindspot.started:
                blindspot.stop()

            try:
                if stage == 'open':
                    # 开盘快速循环: 高频消化 subscribe 回调 (方案A 串行) + 降频轮询
                    open_mon.process_pending()
                    if last_poll is None or (now - last_poll).total_seconds() >= cfg.OPEN_POLL_INTERVAL:
                        res = run_one_round(round_idx, ms, radar, pool,
                                            sentiment, rotations, auction_mon, tail_mon,
                                            stock_ranking, alert_engine, blindspot,
                                            False, now)
                        _print_summary(res, ms)
                        round_idx += 1
                        last_poll = datetime.now()
                    time.sleep(0.1)
                    continue   # 开盘段不走下方 60s sleep
                # 收盘已定格: 后续轮跳过 (避免重复)
                if stage == 'close' and close_done:
                    time.sleep(10); continue
                # 非开盘段: 正常轮询 (close 段 force 定格)
                is_close = (stage == 'close')
                res = run_one_round(round_idx, ms, radar, pool,
                                    sentiment, rotations, auction_mon, tail_mon,
                                    stock_ranking, alert_engine, blindspot,
                                    is_close, now)
                _print_summary(res, ms)
                if is_close:
                    close_done = True
            except Exception:
                logger.exception('轮 {} 失败, 跳过本轮', round_idx)
            round_idx += 1
            # 生产模式 sleep 到下一分钟 (force --rounds 连跑无 sleep)
            if rounds is None:
                _t = datetime.now().time()
                if cfg.OPEN_BURST_START <= _t < cfg.OPEN_BURST_END:
                    logger.debug('开盘加密: sleep {}s', cfg.OPEN_BURST_SLEEP)
                    time.sleep(cfg.OPEN_BURST_SLEEP)
                else:
                    _sleep_until_next_minute()
    finally:
        if open_mon.started:
            open_mon.stop()
        if blindspot.started:
            blindspot.stop()
        close()
        logger.info('===== radar_main 退出 (共 {} 轮) =====', round_idx)


def _sleep_until_next_minute() -> None:
    now = datetime.now()
    secs = 60 - now.second
    logger.info('sleep {}s 至下一分钟整点', secs)
    time.sleep(max(1, secs))


def main():
    parser = argparse.ArgumentParser(description='testv10.2 宽带雷达主循环 (单进程)')
    parser.add_argument('--force', action='store_true', help='跳过交易时段门控 (盘前/验证)')
    parser.add_argument('--rounds', type=int, default=None, help='跑 N 轮退出')
    parser.add_argument('--push', action='store_true', help='真推飞书 (默认 dry-run 禁推, 守红线)')
    args = parser.parse_args()
    run(rounds=args.rounds, force=args.force, push=args.push)


if __name__ == '__main__':
    main()
