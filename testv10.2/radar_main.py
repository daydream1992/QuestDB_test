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
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime, time as dtime  # noqa: E402

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
from rotation_switch import RotationSwitch  # noqa: E402
from board_leaderboard import BoardLeaderboard  # noqa: E402
from open_monitor import OpenMonitor  # noqa: E402
from auction_monitor import AuctionMonitor  # noqa: E402
from tail_monitor import TailMonitor  # noqa: E402
from stock_ranking import StockRanking  # noqa: E402
from blindspot_monitor import BlindspotMonitor  # noqa: E402
from ladder_tracker import LadderTracker  # noqa: E402
from opportunity_engine import OpportunityEngine  # noqa: E402
from duckdb_snapshot import DuckdbSnapshot  # noqa: E402
from alert_engine import AlertEngine  # noqa: E402
import data_provider  # noqa: E402  统一采集层 (bundle)

ROUND_INTERVAL = 60  # 秒 (雷达 1 分钟/轮)

# 文件日志 (盘中长跑可追溯)
os.makedirs(cfg.LOG_DIR, exist_ok=True)
logger.add(os.path.join(cfg.LOG_DIR, 'testv10.2_radar_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')
# 池内 行业:概念 比例 CSV (连续观察, 判定混排是否打架; 见 memory: 行业分层观察)
POOL_RATIO_CSV = os.path.join(cfg.LOG_DIR, 'pool_ratio.csv')
# 心跳文件 (外部监控判"今天跑没跑"; mtime 新鲜度 = 假死检测)
HEARTBEAT_TS = os.path.join(cfg.LOG_DIR, 'heartbeats', 'radar_main.ts')
os.makedirs(os.path.dirname(HEARTBEAT_TS), exist_ok=True)


def _touch_heartbeat() -> None:
    """每轮写心跳时间戳 (轻量 touch, 供 watch_radar.ps1 判假死)。"""
    try:
        with open(HEARTBEAT_TS, 'w', encoding='utf-8') as f:
            f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    except OSError:  # noqa: BLE001  心跳失败不影响雷达
        pass


# 实时状态 (--panel 主循环刷新用): 心跳时间/轮次/耗时/池大小
_status: dict = {'last_round': 0, 'last_dt': 0.0, 'pool': 0, 'hb': ''}
_PANEL = False   # --panel 模式: 主循环每轮刷新状态行


def _status_line(round_idx: int, dt: float, pool_n: int) -> None:
    """覆盖显示单行实时状态 (cmd \r 刷新, 轻量; 仅 --panel 主循环后)。"""
    _status['last_round'] = round_idx
    _status['last_dt'] = dt
    _status['pool'] = pool_n
    _status['hb'] = datetime.now().strftime('%H:%M:%S')
    s = (f"\r  [轮 {_status['last_round']} | 耗时 {_status['last_dt']:.0f}s | "
         f"池 {_status['pool']} | 心跳 {_status['hb']}]")
    try:
        print(s + ' ' * max(0, 40 - len(s)), end='')
    except OSError:  # noqa: BLE001  终端关闭忽略
        pass


def _preflight(push: bool) -> None:
    """--push 启动预检: 校验推送武装状态, 缺关键凭据即 exit(1) 不静默假成功。

    覆盖: ①webhook ②飞书 APP_ID/SECRET ③parquet 映射 ④通达信探针 (tq.initialize)。
    打印 push=ARMED/DRY, 任一 --push 必需项缺失 → 中文提示 + exit(1)。"""
    import os as _os
    from dotenv import load_dotenv
    _dot = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         'config', '.env')
    load_dotenv(_dot)
    webhook = _os.getenv('LARK_WEBHOOK_URL', '')
    app_id = _os.getenv('LARK_APP_ID', '')
    app_secret = _os.getenv('LARK_APP_SECRET', '')
    parquet_ok = _os.path.exists(cfg.MAPPING_PARQUET)
    if not push:
        logger.info('启动预检: dry-run 模式 (禁推, 守红线); webhook={} parquet={}',
                    'SET' if webhook else 'EMPTY', 'OK' if parquet_ok else 'MISSING')
        return
    # --push: 校验武装
    missing = []
    if not webhook:
        missing.append('LARK_WEBHOOK_URL (预警卡 webhook)')
    if not (app_id and app_secret):
        missing.append('LARK_APP_ID/LARK_APP_SECRET (飞书多维表)')
    if not parquet_ok:
        missing.append(f'sector_mapping.parquet (先跑 refresh_mapping.py)')
    if missing:
        logger.error('🚫 --push 启动失败, 缺: {}', '; '.join(missing))
        logger.error('   (dry-run 模式可跳过: python radar_main.py)')
        sys.exit(1)
    # 通达信探针 (COM 在线检查, 失败不 exit 让 init 兜底)
    logger.info('启动预检: push=ARMED (webhook={} 飞书={} parquet={})',
                'SET' if webhook else 'EMPTY', 'SET' if app_id else 'EMPTY', 'OK')


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
                  sentiment, rotations: list, rotation_switch, board_leaderboard,
                  auction_monitor, tail_monitor, stock_ranking, alert_engine,
                  blindspot, opportunity, ladder, duck,
                  is_close: bool, now: datetime) -> dict:
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
        # 全市场 pct 前 N 补钻 (池外最强票可见): 单日最牛股可能不在池板块内
        # 合并进一次 drill_stocks (共用 35s 预算, 防双钻 70s 拖垮轮次)
        global_top = df.sort_values('pct', ascending=False)['code'].tolist()[:cfg.DRILL_GLOBAL_TOP_N]
        all_codes = list(dict.fromkeys(candidates + [c for c in global_top
                                                     if c not in candidates and c in pct_map]))
        drilled = tk.drill_stocks(all_codes, df=df) if all_codes else {}

    # === 分时段统一采集 + 计算层并联 (per-module try 故障隔离) ===
    stage = cfg.get_stage(now)
    bundle = data_provider.fetch_bundle(stage, df)
    # DuckDB 本地快照 (每轮: 池 + 钻取; 情绪在下面计算后)
    if duck:
        try:
            duck.snapshot_pool(pool, now)
            duck.snapshot_drilled(drilled, ms, now)
        except Exception:  # noqa: BLE001
            logger.debug('DuckDB 快照失败, 跳过')
    # 大盘情绪 (close 段 force_push 收盘定格绕门控; 否则 maybe_push)
    if sentiment:
        try:
            if is_close:
                sentiment.force_push(bundle, rows, now)
            else:
                sentiment.maybe_push(bundle, rows, now)
            # DuckDB 情绪快照 (last_result 计算后已有)
            if duck and sentiment.last_result:
                duck.snapshot_sentiment(sentiment.last_result, now)
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
    # 板块高低切 (盘中/tail; 资金从A切向B)
    if stage in ('intraday', 'tail') and rotation_switch:
        try:
            rotation_switch.maybe_push(rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('高低切异常, 跳过 (故障隔离)')
    # 板块内个股梯队 (盘中/tail; 龙头/助攻/跟风 → 表)
    if stage in ('intraday', 'tail') and board_leaderboard:
        try:
            board_leaderboard.maybe_push(pool, drilled, blindspot, rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('板块梯队异常, 跳过 (故障隔离)')
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
    if stage in ('open', 'intraday', 'tail') and blindspot:
        try:
            _k1, _w1, _k2, _w2 = cfg.BLINDSPOT_SORT
            top20 = [c for c, _ in sorted(drilled.items(),
                                          key=lambda kv: kv[1].get(_k1, 0) * _w1
                                          + kv[1].get(_k2, 0) * _w2,
                                          reverse=True)[:cfg.BLINDSPOT_TOPN]]
            blindspot.refresh(top20)
            blindspot.process_pending()   # 盲区回调消化 (串行, 回调不碰 COM)
            # 盲区事件消费: 炸板/回封 → 实时卡 + 涨停梯队落表 (带计数+板块映射)
            for ev in blindspot.drain_events():
                try:
                    # DuckDB 事件快照 (防飞书挂时本地留痕)
                    if duck:
                        duck.record_event(ev['type'], ev['code'], ev['name'],
                                          f"封单{ev.get('prev',0):.0f}→{ev.get('cur',0):.0f}", now)
                    # 涨停梯队落表 (概念/行业映射 + 封板/炸板/回封计数)
                    if ladder:
                        ladder.record_event(ev, blindspot, now)
                    # 实时卡 (≤2/min bucket 天然限频; 带今日计数 + 连板 + 板块)
                    _d = drilled.get(ev['code'], {})
                    _ever = int(_d.get('EverZTCount', 0))
                    _boards = [ms.board_name(b) for b in sorted(ms.boards_of(ev['code']))[:2]] if ms else []
                    if ev['type'] == '炸板':
                        opportunity.pub.on_seal_break(
                            ev['code'], ev['name'], ev['prev'],
                            break_n=blindspot.break_count.get(ev['code'], 0),
                            ever_zt=_ever, boards=_boards, zaf=ev.get('zaf', 0),
                            fhsl=ev.get('fHSL', 0), now=now)
                    elif ev['type'] == '回封':
                        opportunity.pub.on_seal_back(
                            ev['code'], ev['name'], ev['cur'],
                            back_n=blindspot.back_count.get(ev['code'], 0),
                            ever_zt=_ever, boards=_boards, zaf=ev.get('zaf', 0),
                            fhsl=ev.get('fHSL', 0), now=now)
                    elif ev['type'] == '衰竭':
                        opportunity.pub.on_seal_fade(
                            ev['code'], ev['name'], ev['prev'], ev['cur'],
                            ever_zt=_ever, boards=_boards, zaf=ev.get('zaf', 0),
                            fhsl=ev.get('fHSL', 0), now=now)
                except Exception:  # noqa: BLE001  单事件失败不崩
                    logger.debug('盲区事件处理失败: {}', ev)
        except Exception:  # noqa: BLE001
            logger.exception('盲区补盲异常, 跳过 (故障隔离)')
    # 机会事件引擎 (3 正: 新主线/趋势确认/龙头封板; 盘中/tail; 纯内存读 pool/drilled)
    if stage in ('open', 'intraday', 'tail') and opportunity:
        try:
            opportunity.check(new_entries, pool, drilled, blindspot, rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('机会引擎异常, 跳过 (故障隔离)')
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
    _touch_heartbeat()
    # 单轮耗时告警 (接近 60s 预算, 可能轮次重叠)
    _dt = time.time() - t0
    if _dt > 50:
        logger.warning('⚠️ 轮 {} 耗时 {:.0f}s, 接近 60s 预算, 可能轮次重叠!', round_idx, _dt)
    return {'round': round_idx, 'new_entries': new_entries, 'hot': hot,
            'pool_stats': pool.stats(), 'leaders': leaders, '_dt': _dt}


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


def run(rounds: int | None = None, force: bool = False, push: bool = False,
        panel: bool = False) -> None:
    global _PANEL
    _PANEL = panel
    ms = ms_mod.MappingStore(cfg.MAPPING_PARQUET)
    radar = MesoRadar(ms, cfg.MONITOR_LEVELS)
    pool = BoardPool()
    pub = pub_mod.Publisher(dry_run=not push)
    sentiment = SentimentMonitor(ms, pub, dry_run=not push)   # --push 真写飞书+真发警报, 否则 dry-run
    rotations = [RotationMonitor(lvls, label, table_base, dry_run=not push)
                 for label, table_base, lvls in cfg.ROTATION_LEVELS.values()]
    rotation_switch = RotationSwitch(pub, dry_run=not push)   # 板块高低切
    board_leaderboard = BoardLeaderboard(ms, dry_run=not push)  # 板块内个股梯队
    open_mon = OpenMonitor(ms, pub, dry_run=not push)
    auction_mon = AuctionMonitor(dry_run=not push, pub=pub, ms=ms)
    tail_mon = TailMonitor(ms, dry_run=not push)
    stock_ranking = StockRanking(ms, dry_run=not push)
    duck = DuckdbSnapshot(dry_run=not push)   # DuckDB 本地快照 (防飞书挂 + 盘后分析)
    alert_engine = AlertEngine(ms, pub, dry_run=not push, duck=duck)
    blindspot = BlindspotMonitor(ms, dry_run=not push)
    ladder = LadderTracker(ms, dry_run=not push)
    opportunity = OpportunityEngine(ms, pub, dry_run=not push)
    # init 保护: 通达信 COM 初始化失败给友好提示, 不裸 traceback
    try:
        init()
    except Exception as e:  # noqa: BLE001
        logger.error('🚫 通达信初始化失败: {} (请确认通达信客户端已打开)', e)
        sys.exit(1)
    round_idx = 0
    last_poll: datetime | None = None   # 开盘段 run_one_round 降频计时
    close_done = False                   # 收盘定格只跑一次
    logger.info('===== radar_main 启动 (rounds={} force={} push={}) =====', rounds, force, push)
    try:
        while True:
            now = datetime.now()
            # 退出: 跑完指定轮 / 15:00 收盘 (但先跑一次 close 定格)
            if rounds is not None and round_idx >= rounds:
                logger.info('跑完 {} 轮, 退出', rounds); break
            if rounds is None and now.time() >= cfg.TRADING_CLOSE and close_done:
                logger.info('15:00 收盘定格完成, 退出'); break
            # 非交易门控 (生产; force 跳过): 用 get_stage 覆盖竞价+盘中+尾盘+收盘
            if not force and rounds is None:
                if not tc.is_trading_day(now):
                    logger.info('非交易日, 退出'); break
                if cfg.get_stage(now) == 'off':
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
            # 盲区补盲订阅 (open/intraday/tail 启动, 其他时段停止); refresh 在 run_one_round 内
            # 开盘即有涨停股, 6s 监控价值高, 不必等 9:45
            if stage in ('open', 'intraday', 'tail') and not blindspot.started:
                blindspot.started = True
                logger.info('盲区补盲: 进入{}, 待首轮钻取后订阅 Top{}', stage, cfg.BLINDSPOT_TOPN)
            elif stage not in ('open', 'intraday', 'tail') and blindspot.started:
                blindspot.stop()

            try:
                if stage == 'open':
                    # 开盘快速循环: 高频消化 subscribe 回调 (方案A 串行) + 降频轮询
                    open_mon.process_pending()
                    if last_poll is None or (now - last_poll).total_seconds() >= cfg.OPEN_POLL_INTERVAL:
                        res = run_one_round(round_idx, ms, radar, pool,
                                            sentiment, rotations, rotation_switch,
                                            board_leaderboard, auction_mon, tail_mon,
                                            stock_ranking, alert_engine, blindspot,
                                            opportunity, ladder, duck, False, now)
                        _print_summary(res, ms)
                        if _PANEL:
                            _status_line(round_idx, res.get('_dt', 0),
                                         res.get('pool_stats', {}).get('total', 0))
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
                                    sentiment, rotations, rotation_switch,
                                    board_leaderboard, auction_mon, tail_mon,
                                    stock_ranking, alert_engine, blindspot,
                                    opportunity, ladder, duck, is_close, now)
                _print_summary(res, ms)
                if _PANEL:
                    _status_line(round_idx, res.get('_dt', 0),
                                 res.get('pool_stats', {}).get('total', 0))
                if is_close:
                    close_done = True
            except Exception:
                logger.exception('轮 {} 失败, 跳过本轮', round_idx)
            round_idx += 1
            # 生产模式 sleep 到下一分钟 (force --rounds 连跑无 sleep)
            if rounds is None:
                _sleep_until_next_minute()
    finally:
        if open_mon.started:
            open_mon.stop()
        if blindspot.started:
            blindspot.stop()
        if duck:
            duck.close()
        close()
        logger.info('===== radar_main 退出 (共 {} 轮) =====', round_idx)


def _sleep_until_next_minute() -> None:
    now = datetime.now()
    secs = 60 - now.second
    logger.info('sleep {}s 至下一分钟整点', secs)
    time.sleep(max(1, secs))


def _panel_wait(push: bool, target_h: int = 9, target_m: int = 15) -> None:
    """--panel 启动面板: 显示依赖检查 + 倒计时到 9:15 竞价, 归零返回。

    轻量 cmd 终端刷新 (print + \\r, 零依赖)。提前启动时替代门控傻等,
    倒计时结束后自动进入主循环 (9:15 精准进竞价)。依赖检查复用于检查项。"""
    from dotenv import load_dotenv
    import os as _os
    _dot = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         'config', '.env')
    load_dotenv(_dot)
    webhook = _os.getenv('LARK_WEBHOOK_URL', '')
    tdx_ok = _os.path.exists(cfg.MAPPING_PARQUET)
    duck_ok = __import__('duckdb_snapshot', fromlist=['x']) is not None
    print('\n' + '═' * 52)
    print('  v10.2 雷达启动面板 (cmd)')
    print('═' * 52)
    try:
        while True:
            now = datetime.now()
            t = now.time()
            # 已过 9:15 → 直接返回进主循环
            if t >= dtime(target_h, target_m):
                print(f'\n  ✅ 已到 {target_h:02d}:{target_m:02d}, 进入主循环...')
                break
            # 依赖检查 (实时)
            tdx_proc = __import__('subprocess', fromlist=['run'])
            r = tdx_proc.run(['tasklist', '/FI', 'IMAGENAME eq tdxw.exe', '/NH'],
                             capture_output=True, text=True)
            tdx_up = 'tdxw.exe' in r.stdout
            deps = [
                ('通达信客户端', tdx_up, '需打开通达信'),
                ('板块映射 parquet', tdx_ok, '跑 refresh_mapping.py'),
                ('飞书 webhook', bool(webhook), '配 config/.env'),
                ('DuckDB 快照', duck_ok, '依赖 duckdb'),
            ]
            # 倒计时
            target = datetime.combine(now.date(), dtime(target_h, target_m))
            left = (target - now).total_seconds()
            mm, ss = int(left // 60), int(left % 60)
            bar_n = int(20 * (1 - left / 600)) if left < 600 else 0
            bar = '█' * min(bar_n, 20) + '░' * max(0, 20 - min(bar_n, 20))
            mode = '真推飞书' if push else 'dry-run(禁推)'
            lines = [
                f'\r  ⏳ 距竞价 {target_h:02d}:{target_m:02d}  {mm:02d}分{ss:02d}秒  [{bar}]  {mode}',
                '',
            ]
            for name, ok, hint in deps:
                mark = '✅' if ok else '❌'
                lines.append(f'    {mark} {name:<14} {"" if ok else hint}')
            lines.append(f'\r  当前: {now.strftime("%H:%M:%S")}   (提前启动, 9:15 自动进竞价)')
            print('\033[2J\033[H' + '\n'.join(lines))
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n  面板退出 (Ctrl+C), 不启动雷达')
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='testv10.2 宽带雷达主循环 (单进程)')
    parser.add_argument('--force', action='store_true', help='跳过交易时段门控 (盘前/验证)')
    parser.add_argument('--rounds', type=int, default=None, help='跑 N 轮退出')
    parser.add_argument('--push', action='store_true', help='真推飞书 (默认 dry-run 禁推, 守红线)')
    parser.add_argument('--panel', action='store_true', help='启动面板 (倒计时到9:15 + 依赖检查)')
    args = parser.parse_args()
    if args.panel:
        _panel_wait(args.push)
    _preflight(args.push)
    run(rounds=args.rounds, force=args.force, push=args.push, panel=args.panel)


if __name__ == '__main__':
    main()
