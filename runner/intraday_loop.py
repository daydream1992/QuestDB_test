"""盘中主循环

脚本路径: K:\QuestDB_test\\runner\\intraday_loop.py
用途: 9:30-15:00 盘中 10s 主循环, 采集→计算→策略→推送
执行时间: 09:30-11:30 / 13:00-15:00
频率: 10 秒/轮 (可从 strategies.yaml 读取)
流程 (每轮):
  10s 块:
    1. c1_pricevol: 全场价量
    2. c2_snapshot: 重点 500 只快照
    3. c3_more_info: 重点 500 只 88 字段 (mode='intraday')
    4. intraday_engine: 实盘异动检测 (surge/封板/炸板/主力流) + critical 即时飞书
  60s 块 (round_idx % 6 == 0):
    5. c4_kline → k1_indicators → k2_signals
    6. 构建 ctx + k3_sentiment 大盘情绪
    7. 遍历策略 → decisions
    8. 风控 + 情绪门控 + 飞书推送
    9. 板块资金流 + 共振分析
"""

import os
import sys
import time
from datetime import datetime, time as dtime

import pandas as pd

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch  # noqa: E402
from lib.tq_client import init, close  # noqa: E402
from lib import lark  # noqa: E402
from lib.market_clock import is_trading_time, is_trading_day  # noqa: E402

import collect.c1_pricevol as c1  # noqa: E402
import collect.c2_snapshot as c2  # noqa: E402
import collect.c3_more_info as c3  # noqa: E402
import collect.c4_kline as c4  # noqa: E402
import compute.k1_indicators as k1  # noqa: E402
import compute.k2_signals as k2  # noqa: E402
import compute.k3_sentiment as k3  # noqa: E402
import strategy.intraday_engine as intraday_engine  # noqa: E402
from strategy import dark_money  # noqa: E402

from strategy.registry import StrategyRegistry  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402
from strategy.risk import RiskManager  # noqa: E402
from strategy.selector import select_focus_pool  # noqa: E402
from strategy.resonance import scan_market  # noqa: E402
from lib.relation_graph import load_from_json, DEFAULT_JSON_DIR, get_stock_sectors  # noqa: E402

# 配置路径
_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')
_PLUGINS_DIR = os.path.join(_PROJ_ROOT, 'strategy', 'plugins')

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_intraday_loop_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# qd_decisions 列顺序 (与 DDL 06_signals.sql 一致)
_DECISION_COLS = ['decision_time', 'code', 'strategy_name',
                  'action', 'position_size', 'price', 'reason']

# qd_resonance 列顺序 (与 DDL 09_resonance.sql 一致)
_RESONANCE_COLS = ['code', 'resonance_time', 'sector_resonance', 'index_resonance',
                   'macd_resonance', 'volume_resonance', 'flow_resonance',
                   'total_score', 'signal_type', 'description']

# qd_sector_flow 列顺序 (与 DDL 08_flow.sql 一致)
_SECTOR_FLOW_COLS = ['code', 'flow_time', 'main_net', 'big_net', 'mid_net',
                     'small_net', 'dark_money', 'light_money', 'total_flow', 'net_pct']

# qd_money_flow 列顺序 (与 DDL 08_flow.sql 一致)
_MONEY_FLOW_COLS = ['code', 'flow_time', 'main_net', 'big_order_diff',
                    'dark_money', 'light_money', 'pressure_diff_5level',
                    'buy_pressure', 'sell_pressure', 'net_flow']

# 情绪门控: emotion_order <= 此值时拦截 buy (0冰点/1低迷)
EMOTION_BLOCK_BUY_ORDER = 1


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_yaml():
    """读取 strategies.yaml"""
    import yaml
    try:
        with open(_YAML_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning('读取 {} 失败, 用默认值: {}', _YAML_PATH, e)
        return {}


def _get_all_stock_codes(con):
    """从注册表取所有股票代码"""
    try:
        df = query_df(con, "SELECT code FROM qd_code_registry WHERE code_type = 'stock'")
        if not df.empty:
            return df['code'].tolist()
    except Exception as e:
        logger.warning('查询注册表失败: {}', e)
    return []


def _get_focus_codes(con, all_stocks):
    """动态选重点池, 失败回退全股票"""
    if not all_stocks:
        return []
    try:
        pv = query_df(con,
                      "SELECT * FROM qd_pricevol "
                      "WHERE snapshot_time > dateadd('m', -5, now())")
        mi = query_df(con,
                      "SELECT * FROM qd_stock_daily "
                      "WHERE date > dateadd('d', -2, now())")
        focus = select_focus_pool(pv, mi)
        return focus if focus else all_stocks
    except Exception as e:
        logger.warning('选股器失败, 回退全股票: {}', e)
        return all_stocks


def _build_context(con, graph):
    """构建策略上下文 (一次采集全策略共享)"""
    ctx = StrategyContext(timestamp=datetime.now(), is_trading=True)
    ctx.graph = graph
    try:
        ctx.pricevol_df = query_df(
            con, "SELECT * FROM qd_pricevol "
                 "WHERE snapshot_time > dateadd('m', -5, now())")
    except Exception:
        pass
    try:
        ctx.snapshot_focus_df = query_df(
            con, "SELECT * FROM qd_stock_snapshot "
                 "WHERE snapshot_time > dateadd('m', -5, now())")
    except Exception:
        pass
    try:
        # c3 daily 写 qd_stock_daily (含 ZTPrice/fLianB/CJJEPre1 等日级字段)
        # c3 intraday 写 qd_stock_snapshot (含 ZAF/fHSL/Zjl 等实时字段, 由 snapshot_focus_df 承载)
        ctx.more_info_df = query_df(
            con, "SELECT * FROM qd_stock_daily "
                 "WHERE date > dateadd('d', -2, now())")
    except Exception:
        pass
    try:
        ctx.indicators_df = query_df(
            con, "SELECT * FROM qd_indicators "
                 "WHERE calc_time > dateadd('m', -30, now())")
    except Exception:
        pass
    try:
        ctx.signals_df = query_df(
            con, "SELECT * FROM qd_signals "
                 "WHERE signal_time > dateadd('m', -10, now())")
    except Exception:
        pass
    # 大盘指数快照
    try:
        idx_df = query_df(
            con, "SELECT * FROM qd_index_snapshot "
                 "WHERE snapshot_time > dateadd('m', -2, now())")
        if not idx_df.empty:
            idx_df = idx_df.sort_values('snapshot_time') \
                .groupby('code', as_index=False).last()
            ctx.index_snapshot = {
                r['code']: {'Now': r.get('Now'), 'LastClose': r.get('LastClose')}
                for _, r in idx_df.iterrows()}
    except Exception:
        pass
    # 竞价数据 (p09/p10/p11)
    try:
        ctx.auction_df = query_df(
            con, "SELECT * FROM qd_auction_snapshot "
                 "WHERE auction_time > dateadd('m', -30, now())")
    except Exception:
        pass
    # 大单事件 (p12)
    try:
        ctx.big_order_df = query_df(
            con, "SELECT * FROM qd_big_order "
                 "WHERE order_time > dateadd('m', -30, now())")
    except Exception:
        pass
    # 个股资金流 (p08/p12)
    try:
        ctx.money_flow_df = query_df(
            con, "SELECT * FROM qd_money_flow "
                 "WHERE flow_time > dateadd('m', -10, now())")
    except Exception:
        pass
    # 板块资金流 (p07)
    try:
        ctx.sector_flow_df = query_df(
            con, "SELECT * FROM qd_sector_flow "
                 "WHERE flow_time > dateadd('m', -10, now())")
    except Exception:
        pass
    # 共振分析 (p06)
    try:
        ctx.resonance_df = query_df(
            con, "SELECT * FROM qd_resonance "
                 "WHERE resonance_time > dateadd('m', -30, now())")
    except Exception:
        pass
    return ctx


def _process_decisions(con, decisions, risk, ctx=None):
    """风控过滤 + 情绪门控 + 写 qd_decisions + 飞书推送

    Args:
        con: psycopg2 连接
        decisions: list[Decision]
        risk: RiskManager
        ctx: StrategyContext (用于情绪门控; None 则不门控)
    """
    if not decisions:
        return
    now = datetime.now()
    emotion = getattr(ctx, 'emotion_rating', None) if ctx else None
    rows = []
    for d in decisions:
        # buy: 情绪门控 (大盘冰点/低迷) + 仓位风控
        if d.action == 'buy':
            if emotion is not None and emotion <= EMOTION_BLOCK_BUY_ORDER:
                logger.info('情绪门控拦截 buy {} {} (大盘冰点/低迷 order={})',
                            d.code, d.strategy, emotion)
                continue
            if not risk.can_open(d.position_pct):
                logger.info('风控拦截 buy {} {} (仓位 {}%)',
                            d.code, d.strategy, d.position_pct)
                continue
        reason = d.reason
        if d.score:
            reason = '{} [评分{:.0f}]'.format(reason, d.score)
        rows.append((now, d.code, d.strategy, d.action,
                     d.position_pct, d.price, reason))
        # 飞书推送 buy/sell 决策
        if d.action in ('buy', 'sell'):
            try:
                lark.push_decision({
                    'decision_time': now,
                    'code': d.code,
                    'strategy_name': d.strategy,
                    'action': d.action,
                    'position_size': d.position_pct,
                    'price': d.price,
                    'reason': reason,
                })
            except Exception as e:
                logger.warning('飞书推送失败 {} {}: {}', d.code, d.action, e)
    if rows:
        n = executemany_batch(con, 'qd_decisions', _DECISION_COLS, rows)
        logger.info('写入 qd_decisions: {} 行', n)


def _run_resonance(con, ctx):
    """共振分析 → qd_resonance (60s/轮)"""
    try:
        df = scan_market(ctx.pricevol_df, ctx.index_snapshot, ctx.graph)
        if df is None or df.empty:
            return
        now = datetime.now()
        rows = []
        for _, r in df.iterrows():
            score = _safe_float(r.get('resonance_score'))
            if score >= 80:
                sig = 'strong_buy'
            elif score >= 60:
                sig = 'buy'
            elif score >= 40:
                sig = 'watch'
            else:
                sig = 'sell'
            rows.append((r['code'], now, None, None, None, None, None,
                         score, sig, str(r.get('reason', ''))))
        executemany_batch(con, 'qd_resonance', _RESONANCE_COLS, rows)
        logger.info('写入 qd_resonance: {} 行', len(rows))
    except Exception as e:
        logger.warning('共振分析失败: {}', e)


def _run_sector_flow(con, ctx):
    """板块资金流 → qd_sector_flow (60s/轮)

    按 snapshot_focus_df.Zjl 聚合每板块主力净流入, 写 qd_sector_flow。
    (C5 修复: Zjl 在 qd_stock_snapshot intraday 字段, 不在 qd_stock_daily;
     旧版读 more_info_df(qd_stock_daily) 永远取不到 Zjl → sector_flow 永不写。
     改读 snapshot_focus_df。注: snapshot 双形态行问题见 ARCHITECTURE_REVIEW C8)
    """
    sf_src = ctx.snapshot_focus_df
    if ctx.graph is None or sf_src is None or sf_src.empty:
        return
    if 'Zjl' not in sf_src.columns:
        return
    try:
        mi = sf_src
        sector_agg = {}  # block_code → {main_net, total_flow, count}
        for _, r in mi.iterrows():
            code = r.get('code')
            if not code:
                continue
            zjl = _safe_float(r.get('Zjl'))
            amt = _safe_float(r.get('Amount'))
            sectors = get_stock_sectors(code)
            for s in (sectors or []):
                bc = s.get('block_code')
                if not bc:
                    continue
                agg = sector_agg.setdefault(
                    bc, {'main_net': 0.0, 'total_flow': 0.0, 'count': 0})
                agg['main_net'] += zjl
                agg['total_flow'] += amt
                agg['count'] += 1
        if not sector_agg:
            return
        now = datetime.now()
        rows = []
        for bc, agg in sector_agg.items():
            net_pct = (agg['main_net'] / agg['total_flow'] * 100
                       if agg['total_flow'] > 0 else 0.0)
            rows.append((bc, now, agg['main_net'], None, None, None,
                         None, None, agg['total_flow'], net_pct))
        executemany_batch(con, 'qd_sector_flow', _SECTOR_FLOW_COLS, rows)
        logger.info('写入 qd_sector_flow: {} 行', len(rows))
    except Exception as e:
        logger.warning('板块资金流失败: {}', e)


def _run_money_flow(con, ctx):
    """个股明暗资金 → qd_money_flow (60s/轮), 并刷新 ctx.money_flow_df 供 p08/p12 当轮读取

    读 ctx.snapshot_focus_df (qd_stock_snapshot: c2 5档+NowVol / c3 intraday Zjl 等)。
    C8 应对: intraday 字段 (c3@T+1s) 按 code 回填到同轮 c2 行 (bfill 取同轮 c3, ffill 兜底),
    再只留 c2 行 (NowVol 非空) —— 既保留多轮时间序列 (cancel_diff 可差分), 又让每 c2 行带最新 Zjl。
    重赋 ctx.money_flow_df 避免 H4 滞后一轮 + QuestDB 写读延迟。
    """
    snap = ctx.snapshot_focus_df
    if snap is None or snap.empty:
        return
    try:
        df = snap.copy()
        df = df.sort_values(['code', 'snapshot_time'])
        # C8: c2@T 与同轮 c3@T+1s 配对 → 用 bfill 把同轮 c3 的 intraday 字段回填到 c2 行
        # (ffill 取上一轮会陈旧, 且窗口滑动时边界行 main_net 退化为 0; bfill 同轮最新)
        for c in ('Zjl', 'Zjl_HB', 'FCAmo', 'FCb', 'Wtb'):
            if c in df.columns:
                df[c] = df.groupby('code')[c].bfill().ffill()
        # 只留 c2 行 (有 NowVol), 保证 cancel_diff 差分连续、不被 c3 None 行污染
        if 'NowVol' in df.columns:
            df = df[df['NowVol'].notna()]
        if df.empty:
            return
        mf = dark_money.calc_batch(df, None)  # ffill 已嵌 intraday 字段, 跳过内部 merge
        if mf is None or mf.empty:
            return
        # 构造行 (big_order_diff/light_money 显式 None, 不写 pandas NaN; 仿 _run_sector_flow)
        rows = [
            (r.code, r.flow_time, r.main_net, None, r.dark_money, None,
             r.pressure_diff_5level, r.buy_pressure, r.sell_pressure, r.net_flow)
            for r in mf[_MONEY_FLOW_COLS].itertuples(index=False)
        ]
        executemany_batch(con, 'qd_money_flow', _MONEY_FLOW_COLS, rows)
        ctx.money_flow_df = mf  # 当轮刷新, 供 p08/p12 + H1 首轮校验
        logger.info('写入 qd_money_flow: {} 行', len(rows))
    except Exception as e:
        logger.warning('个股资金流失败: {}', e)


def run(con=None):
    """盘中主循环

    Args:
        con: psycopg2 连接, None 则自建
    """
    logger.info('===== intraday_loop 启动 {} =====', datetime.now())
    own_con = con is None
    if own_con:
        con = connect()

    # 加载调度频率 + 风控配置 + 订阅池
    cfg = _load_yaml()
    sched = cfg.get('schedule', {})
    interval = int(sched.get('pricevol_interval', 10))
    kline_interval = int(sched.get('kline_interval', 60))
    kline_every = max(1, kline_interval // interval)
    watchlist = cfg.get('watchlist', []) or []

    risk_cfg = cfg.get('risk', {})
    risk = RiskManager(
        max_total_position=risk_cfg.get('max_total_position', 80),
        max_single_position=risk_cfg.get('max_single_position', 30),
    )

    # 加载策略插件 + 配置
    StrategyRegistry.load_plugins(_PLUGINS_DIR)
    StrategyRegistry.load_config(_YAML_PATH)
    logger.info('策略加载完成: 启用 {} 个; watchlist {} 只',
                len(StrategyRegistry.get_all()), len(watchlist))

    # 加载关系图谱 (盘中复用内存映射, 加载失败降级)
    graph = None
    try:
        load_from_json(DEFAULT_JSON_DIR)
        graph = True
        logger.info('关系图谱加载完成')
    except Exception as e:
        logger.warning('关系图谱加载失败, 板块资金流/共振将降级: {}', e)

    round_idx = 0
    fields_checked = False  # H1 护栏: required_fields 仅首轮校验一次
    try:
        while True:
            now = datetime.now()
            # 退出条件: 非交易日 或 15:00 后
            if not is_trading_day(now):
                logger.info('非交易日, 退出主循环')
                break
            if now.time() >= dtime(15, 0):
                logger.info('15:00 后, 退出主循环')
                break
            # 非交易时段 (午间休市等), 短暂等待
            if not is_trading_time(now):
                time.sleep(interval)
                continue

            t0 = time.time()
            logger.info('--- 第 {} 轮 {} ---',
                        round_idx, now.strftime('%H:%M:%S'))

            # 每轮从数据库重新读取全场代码 (避免内存缓存过期)
            all_stocks = _get_all_stock_codes(con)

            # === 10s 采集 + 实盘异动 ===
            try:
                c1.run(con=con)
            except Exception as e:
                logger.error('c1 失败: {}', e)

            focus = _get_focus_codes(con, all_stocks)

            try:
                c2.run(focus_codes=focus, all_codes=all_stocks, con=con)
            except Exception as e:
                logger.error('c2 失败: {}', e)

            try:
                c3.run(focus, mode='intraday', con=con)
            except Exception as e:
                logger.error('c3 失败: {}', e)

            # 实盘异动检测 (10s, c2+c3 后; MonitorState 跨轮, critical 即时飞书)
            try:
                snap = query_df(con, "SELECT * FROM qd_stock_snapshot "
                                    "WHERE snapshot_time > dateadd('s', -30, now())")
                if snap is not None and not snap.empty:
                    intraday_engine.run(con, snap, watchlist)
            except Exception as e:
                logger.error('intraday_engine 失败: {}', e)

            # === 60s 任务: K 线 → 指标 → 信号 → 情绪 → 策略 ===
            # k4→k1→k2 必须按顺序执行, 因为 k2 依赖 k1 产出 qd_signals
            # 策略 ctx 也在此块构建, 依赖 k2 的 qd_signals
            if round_idx % kline_every == 0:
                try:
                    c4.run(all_stocks, period='1m', count=1, con=con)
                except Exception as e:
                    logger.error('c4 1m 失败: {}', e)
                try:
                    c4.run(all_stocks, period='5m', count=1, con=con)
                except Exception as e:
                    logger.error('c4 5m 失败: {}', e)
                try:
                    k1.run(con=con)
                except Exception as e:
                    logger.error('k1 失败: {}', e)
                try:
                    k2.run(con=con)
                except Exception as e:
                    logger.error('k2 失败: {}', e)
                # 构建 ctx (依赖 qd_signals 已有数据)
                ctx = _build_context(con, graph)
                # 个股明暗资金 → qd_money_flow + 刷新 ctx.money_flow_df (须在策略遍历前, 供 p08/p12)
                _run_money_flow(con, ctx)
                # H1 护栏: 首轮校验策略 required_fields 是否在 ctx 列中 (一次性, 暴露字段错配)
                if not fields_checked:
                    missing = StrategyRegistry.validate_required_fields(ctx)
                    if missing:
                        logger.warning('字段护栏: {} 处 required_fields 缺失 (见上文 error 日志)',
                                       len(missing))
                    fields_checked = True
                # k3 大盘情绪 (挂 ctx.sentiment 供 p17/p18 + buy 门控; 必须在遍历策略前)
                try:
                    snt = k3.run(con, ctx)
                    ctx.sentiment = snt
                    ctx.emotion_rating = snt.get('emotion_order')
                    ctx.divergence_signals = snt.get('divergences')
                except Exception as e:
                    logger.error('k3 情绪失败: {}', e)
                # 遍历策略
                decisions = []
                for name, cls in StrategyRegistry.get_all().items():
                    try:
                        ds = cls().evaluate(ctx)
                        if ds:
                            decisions.extend(ds)
                    except Exception as e:
                        logger.error('策略 {} 评估失败: {}', name, e)
                if decisions:
                    logger.info('策略产出 {} 条决策', len(decisions))
                # 风控 + 情绪门控 + 飞书推送
                _process_decisions(con, decisions, risk, ctx)
                # 板块资金流 → 共振（共振依赖 sector_flow_df，必须先写流再读流）
                _run_sector_flow(con, ctx)
                _run_resonance(con, ctx)

            # 轮次计数放在最后 (确保 k4%6 在本轮开始时正确判定)
            round_idx += 1

            # === 控频 ===
            elapsed = time.time() - t0
            sleep_s = max(0.5, interval - elapsed)
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        logger.info('Ctrl+C 退出盘中主循环')
    finally:
        logger.info('===== intraday_loop 退出 (共 {} 轮) =====', round_idx)
        if own_con:
            con.close()


def main():
    init()
    try:
        run()
    finally:
        close()


if __name__ == '__main__':
    main()
