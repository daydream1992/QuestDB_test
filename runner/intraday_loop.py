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

from lib.qdb import connect, query_df, executemany_batch, cutoff  # noqa: E402
from lib.tq_client import init, close  # noqa: E402
import importlib as _il
_feishu = _il.import_module('feishu')  # noqa: E402
from lib.market_clock import is_trading_time, is_trading_day  # noqa: E402

import collect.c1_pricevol as c1  # noqa: E402
import collect.c2_snapshot as c2  # noqa: E402
import collect.c3_more_info as c3  # noqa: E402
import collect.c4_kline as c4  # noqa: E402
import compute.k1_indicators as k1  # noqa: E402
import compute.k2_signals as k2  # noqa: E402
import compute.k3_sentiment as k3  # noqa: E402
import compute.k4_sentiment as k4  # noqa: E402
import compute.k4_sector_heatmap as k4_heatmap  # noqa: E402
import compute.k4_ladder_tracker as k4_ladder  # noqa: E402
import compute.k5_kline_synth as k5  # noqa: E402
import strategy.intraday_engine as intraday_engine  # noqa: E402
from strategy import dark_money  # noqa: E402
from strategy import big_order  # noqa: E402
from strategy import sector_flow as sector_flow_mod  # noqa: E402

from strategy.registry import StrategyRegistry  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402
from strategy.risk import RiskManager  # noqa: E402
from strategy.selector import select_focus_pool  # noqa: E402
from strategy.resonance import scan_market  # noqa: E402
from lib.relation_graph import load_from_json, DEFAULT_JSON_DIR, get_stock_sectors, get_stock_name  # noqa: E402

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

# qd_big_order 列顺序 (与 DDL 11_big_order.sql 一致)
_BIG_ORDER_COLS = ['code', 'order_time', 'order_type', 'price', 'volume',
                   'amount', 'order_level', 'broker']

# 模块级缓存: 注册表代码列表 (每 300s 刷新)
_CACHED_CODES = None
_CACHED_TS = 0.0

# 板块资金流历史 (per block_code 保留最近 _SECTOR_FLOW_HISTORY_LEN 期, 供 _run_rotation detect 用)
_SECTOR_FLOW_HISTORY: dict = {}  # block_code → list[{block_code, net_flow, flow_strength, avg_change, flow_time}]
_SECTOR_FLOW_HISTORY_LEN = 5

# Alpha 引擎 (延时初始化, 进程内单例)
_alpha_engine = None


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _write_heartbeat(name):
    """写入心跳文件 (logs/heartbeats/{name}.ts)"""
    import os
    hb_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'heartbeats')
    os.makedirs(hb_dir, exist_ok=True)
    try:
        with open(os.path.join(hb_dir, f'{name}.ts'), 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass


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
    """从注册表取所有股票代码 + 板块代码 + 指数代码

    结果缓存 _CACHED_CODES, 每 300s 刷新一次 (避免每轮查 DB)。
    """
    now = time.time()
    if _CACHED_CODES is not None and now - _CACHED_TS < 300:
        return _CACHED_CODES
    try:
        df = query_df(con, "SELECT code FROM qd_code_registry WHERE code_type IN ('stock', 'sector', 'index')")
        if not df.empty:
            _CACHED_CODES = df['code'].tolist()
            _CACHED_TS = now
            return _CACHED_CODES
    except Exception as e:
        logger.warning('查询注册表失败: {}', e)
    return []


def _get_focus_codes(con, all_stocks):
    """动态选重点池, 失败回退全股票"""
    if not all_stocks:
        return []
    try:
        pv = query_df(con,
                      f"SELECT * FROM qd_pricevol "
                      f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        mi = query_df(con,
                      f"SELECT * FROM qd_stock_daily "
                      f"WHERE date > '{cutoff(days=2)}'")
        focus = select_focus_pool(pv, mi)
        # 把板块/指数也加到 focus 池 (即使不在 pricevol 中)
        try:
            sector_codes = query_df(con, "SELECT code FROM qd_code_registry WHERE code_type IN ('sector', 'index')")
            if sector_codes is not None and not sector_codes.empty:
                extra = [c for c in sector_codes['code'].tolist() if c not in focus]
                focus = focus + extra if focus else extra
        except Exception:
            pass
        return focus if focus else all_stocks
    except Exception as e:
        logger.warning('选股器失败, 回退全股票: {}', e)
        return all_stocks


def _merge_intraday(snap, intra):
    """C8 拆表后: 把 qd_stock_intraday (每 code 最新) merge 进快照表, 返回完整字段 df

    两表 snapshot_time 差几十秒 (c2/c3 各自时间戳), 按 code 配对同轮, 不按精确 timestamp。
    """
    if snap is None or snap.empty or intra is None or intra.empty:
        return snap
    if 'code' not in intra.columns:
        return snap
    intra_latest = intra.sort_values('snapshot_time') \
        .groupby('code', as_index=False).tail(1)
    intra_cols = [c for c in intra_latest.columns if c not in ('code', 'snapshot_time')]
    snap2 = snap.drop(columns=[c for c in intra_cols if c in snap.columns])
    return snap2.merge(intra_latest[['code'] + intra_cols], on='code', how='left')


def _build_context(con, graph):
    """构建策略上下文 (一次采集全策略共享)"""
    ctx = StrategyContext(timestamp=datetime.now(), is_trading=True)
    ctx.graph = graph
    try:
        ctx.pricevol_df = query_df(
            con, f"SELECT * FROM qd_pricevol "
                 f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
    except Exception:
        pass
    try:
        ctx.snapshot_focus_df = query_df(
            con, f"SELECT * FROM qd_stock_snapshot "
                 f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
    except Exception:
        pass
    # C8 拆表修复: 读 qd_stock_intraday, merge 进 snapshot_focus_df (按 code 取最新)
    try:
        intra = query_df(
            con, f"SELECT * FROM qd_stock_intraday "
                 f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        ctx.intraday_df = intra
        ctx.snapshot_focus_df = _merge_intraday(ctx.snapshot_focus_df, intra)
    except Exception as e:
        logger.warning('intraday merge 失败: {}', e)
    # GP 日级数据 (连板率/次日红盘率/机构等, 盘前/盘后用): 每 code 最新 date
    try:
        ctx.gp_df = query_df(con, f"SELECT * FROM qd_stock_gpjy "
                              f"WHERE date > '{cutoff(days=30)}'")
    except Exception as e:
        logger.warning('GP 加载失败: {}', e)
    # 龙虎榜 (T+1, p13/p14 用): 从 qd_lhb_detail 聚合每 code 最新一日席位 → ctx.lhb_data
    try:
        from strategy.lhb_analyzer import build_lhb_data
        ctx.lhb_data = build_lhb_data(con)
    except Exception as e:
        logger.warning('lhb_data 加载失败 (p13/p14 将空返): {}', e)
    try:
        # c3 daily 写 qd_stock_daily (含 ZTPrice/fLianB/CJJEPre1 等日级字段)
        # c3 intraday 写 qd_stock_snapshot (含 ZAF/fHSL/Zjl 等实时字段, 由 snapshot_focus_df 承载)
        ctx.more_info_df = query_df(
            con, f"SELECT * FROM qd_stock_daily "
                 f"WHERE date > '{cutoff(days=2)}'")
    except Exception:
        pass
    try:
        ctx.indicators_df = query_df(
            con, f"SELECT * FROM qd_indicators "
                 f"WHERE calc_time > '{cutoff(minutes=30)}'")
    except Exception:
        pass
    try:
        ctx.signals_df = query_df(
            con, f"SELECT * FROM qd_signals "
                 f"WHERE signal_time > '{cutoff(minutes=10)}'")
    except Exception:
        pass
    # 大盘指数快照
    try:
        idx_df = query_df(
            con, f"SELECT * FROM qd_index_snapshot "
                 f"WHERE snapshot_time > '{cutoff(minutes=2)}'")
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
            con, f"SELECT * FROM qd_auction_snapshot "
                 f"WHERE auction_time > '{cutoff(minutes=30)}'")
    except Exception:
        pass
    # 大单事件 (p12)
    try:
        ctx.big_order_df = query_df(
            con, f"SELECT * FROM qd_big_order "
                 f"WHERE order_time > '{cutoff(minutes=30)}'")
    except Exception:
        pass
    # 个股资金流 (p08/p12)
    try:
        ctx.money_flow_df = query_df(
            con, f"SELECT * FROM qd_money_flow "
                 f"WHERE flow_time > '{cutoff(minutes=10)}'")
    except Exception:
        pass
    # 板块资金流 (p07)
    try:
        ctx.sector_flow_df = query_df(
            con, f"SELECT * FROM qd_sector_flow "
                 f"WHERE flow_time > '{cutoff(minutes=10)}'")
    except Exception:
        pass
    # 共振分析 (p06)
    try:
        ctx.resonance_df = query_df(
            con, f"SELECT * FROM qd_resonance "
                 f"WHERE resonance_time > '{cutoff(minutes=30)}'")
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
    rows = []
    # 构造信号列表供 log_signals 批量写入飞书
    feishu_signals = []
    for d in decisions:
        # buy: 仓位风控 + 持仓管理
        if d.action == 'buy':
            if not risk.can_open(d.code, d.position_pct):
                logger.info('风控拦截 buy {} {} (仓位 {}%)',
                            d.code, d.strategy, d.position_pct)
                continue
            risk.add_position({
                'code': d.code, 'entry_price': d.price,
                'position_pct': d.position_pct,
                'stop_loss': d.stop_loss or 5,
                'stop_profit': d.stop_profit or 10,
            }, con=con)
        # sell: 移除持仓
        if d.action == 'sell':
            risk.remove_position(d.code, con=con)
        reason = d.reason
        if d.score:
            reason = '{} [评分{:.0f}]'.format(reason, d.score)
        rows.append((now, d.code, d.strategy, d.action,
                     d.position_pct, d.price, reason))
        # 信号收集: buy/sell 必推, watch/warn 走频控
        if d.action in ('buy', 'sell', 'watch', 'warn'):
            from lib.notify_dedup import allow_push
            if d.action in ('watch', 'warn'):
                if not allow_push(d.code, d.action):
                    logger.debug('飞书频控拦截 {} {} (180s TTL 内已推过)', d.code, d.action)
                    continue
            feishu_signals.append({
                'decision_time': now,
                'code': d.code,
                'stock_name': get_stock_name(d.code),
                'strategy_name': d.strategy,
                'action': d.action,
                'position_size': d.position_pct,
                'price': d.price,
                'reason': reason,
            })
    # 批量写入飞书 (聚合推送 + Sheet + Bitable)
    if feishu_signals:
        try:
            # 1. 入桶聚合推送 (5min 一张卡, 解决刷屏)
            for s in feishu_signals:
                _feishu.push_decision_aggregated(s)
            # 2. 表格写入 (推送=False, 表格=True)
            _feishu.log_signals(feishu_signals, push=False, sheet=True, bitable=True)
        except Exception as e:
            logger.warning('飞书写入失败: {}', e)
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
    """板块资金流 → qd_sector_flow (60s/轮), 并返回 per-block agg 给 _run_rotation 用

    按 snapshot_focus_df.Zjl 聚合每板块主力净流入, 写 qd_sector_flow。
    (C5 修复: Zjl 在 qd_stock_snapshot intraday 字段, 不在 qd_stock_daily;
     旧版读 more_info_df(qd_stock_daily) 永远取不到 Zjl → sector_flow 永不写。
     改读 snapshot_focus_df。注: snapshot 双形态行问题见 ARCHITECTURE_REVIEW C8)

    Returns:
        dict[block_code, dict]: 本轮每板块聚合 {main_net, total_flow, count},
            供 _run_rotation 接 history 用。无数据返回 {}。
    """
    sf_src = ctx.snapshot_focus_df
    if ctx.graph is None or sf_src is None or sf_src.empty:
        return {}
    if 'Zjl' not in sf_src.columns:
        return {}
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
                _zjl = _safe_float(zjl)
                if _zjl != 0 and (_zjl == _zjl):  # 跳过 NaN 和 0 (非 focus 池无 Zjl)
                    agg['main_net'] += _zjl
                _amt = _safe_float(amt)
                if _amt == _amt:  # 跳过 NaN
                    agg['total_flow'] += _amt
                agg['count'] += 1
        if not sector_agg:
            return {}
        now = datetime.now()
        rows = []
        for bc, agg in sector_agg.items():
            net_pct = (agg['main_net'] / agg['total_flow'] * 100
                       if agg['total_flow'] > 0 else 0.0)
            rows.append((bc, now, agg['main_net'], None, None, None,
                         None, None, agg['total_flow'], net_pct))
        executemany_batch(con, 'qd_sector_flow', _SECTOR_FLOW_COLS, rows)
        logger.info('写入 qd_sector_flow: {} 行', len(rows))
        return sector_agg
    except Exception as e:
        logger.warning('板块资金流失败: {}', e)
        return {}


def _run_rotation(sector_agg_now):
    """板块轮动检测 → ctx.rotation_signal (60s/轮)

    维护模块级 _SECTOR_FLOW_HISTORY 累积每板块最近 N 期;
    每期对每板块调 sector_flow.detect_rotation (需 ≥2 期), 取 |delta| 最大的轮动信号。
    数据不足返回 None (不影响其他策略, p05 自带 insufficient 兜底)。

    Args:
        sector_agg_now: dict[block_code, {main_net, total_flow, count}] — _run_sector_flow 返回值

    Returns:
        dict|None: 选中的最强轮动信号, 含 block_code/type/prev_flow/curr_flow/delta/reason。
                    数据不足返回 None, 不动 ctx.rotation_signal (由调用方决定)。
    """
    if not sector_agg_now:
        return None
    try:
        from datetime import datetime as _dt
        now_ts = _dt.now()
        best = None  # (|delta|, signal_dict)
        for bc, agg in sector_agg_now.items():
            net_flow = _safe_float(agg.get('main_net'))
            total_flow = _safe_float(agg.get('total_flow'))
            flow_strength = net_flow / total_flow if total_flow > 0 else 0.0
            entry = {
                'block_code': bc,
                'net_flow': net_flow,
                'flow_strength': round(flow_strength, 4),
                'avg_change': 0.0,  # _run_sector_flow 当前不聚合 avg_change, detect_rotation 不强需
                'flow_time': now_ts,
            }
            hist = _SECTOR_FLOW_HISTORY.setdefault(bc, [])
            hist.append(entry)
            if len(hist) > _SECTOR_FLOW_HISTORY_LEN:
                hist.pop(0)  # 截断, 防内存膨胀
            if len(hist) < 2:
                continue
            sig = sector_flow_mod.detect_rotation(hist)
            if not sig or sig.get('type') == 'insufficient':
                continue
            delta = abs(_safe_float(sig.get('delta')))
            # 优先选 |delta| 最大 (含正负方向)
            if best is None or delta > best[0]:
                best = (delta, sig)
        if best is None:
            return None
        return best[1]
    except Exception as e:
        logger.warning('板块轮动检测失败: {}', e)
        return None


def _run_big_order(con, ctx):
    """大单检测 → qd_big_order (60s/轮), 刷新 ctx.big_order_df 供 p12 当轮读取

    输入: ctx.snapshot_focus_df (qd_stock_snapshot: c2 Amount/Now + c3 intraday Zjl)
    C8 应对: 同 _run_money_flow, intraday 字段 bfill+ffill 同轮回填到 c2 行, 再过滤 NowVol 非空。
    DDL 对齐: detect 输出 direction/level → order_type/order_level; neutral 方向不写 (DDL 仅 buy/sell);
              volume = amount / price 估算 (detect 不直出); broker=None (无 L2 数据源)。
    """
    snap = ctx.snapshot_focus_df
    if snap is None or snap.empty:
        return
    try:
        # C8 拆表后: snapshot_focus_df 已含快照列 + merge 的 intraday 列, 不再 bfill/filter
        df = snap.copy().sort_values(['code', 'snapshot_time'])
        # 按 code 分组 → 每组按 snapshot_time 排序 → 相邻帧 detect
        events = []
        for code, g in df.groupby('code', sort=False):
            frames = g.to_dict('records')
            evs = big_order.detect_batch(code, frames)
            for ev in evs:
                direction = ev.get('direction')
                if direction not in ('buy', 'sell'):
                    continue  # neutral 跳过 (DDL 仅 buy/sell)
                price = _safe_float(ev.get('price'))
                amount_diff = _safe_float(ev.get('amount_diff'))
                volume = int(round(amount_diff / price)) if price > 0 else 0
                events.append({
                    'code': code,
                    'order_time': ev.get('time'),
                    'order_type': direction,
                    'price': price,
                    'volume': volume,
                    'amount': amount_diff,
                    'order_level': ev.get('level'),
                    'broker': None,
                })
        if not events:
            return
        rows = [(e['code'], e['order_time'], e['order_type'], e['price'],
                 e['volume'], e['amount'], e['order_level'], e['broker'])
                for e in events]
        executemany_batch(con, 'qd_big_order', _BIG_ORDER_COLS, rows)
        # 刷新 ctx.big_order_df 供 p12 当轮 + H1 首轮校验
        import pandas as _pd
        ctx.big_order_df = _pd.DataFrame(rows, columns=_BIG_ORDER_COLS)
        logger.info('写入 qd_big_order: {} 行', len(rows))
    except Exception as e:
        logger.warning('大单检测失败: {}', e)


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
        # C8 拆表后: snapshot_focus_df 已含快照列 + merge 进的 intraday 列 (Zjl/FCAmo 等)
        # 不再 bfill / filter NowVol (都是 c2 行, intraday 已真实)
        df = snap.copy().sort_values(['code', 'snapshot_time'])
        mf = dark_money.calc_batch(df, None)  # df 已嵌 intraday 字段, 跳过内部 merge
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


def run(con=None, max_rounds=None, force=False):
    """盘中主循环

    Args:
        con: psycopg2 连接, None 则自建
        max_rounds: 跑完 N 轮后退出 (None=无限, 盘中正常用); 盘后验证给 1
        force: True 时跳过 is_trading_day/is_trading_time/15:00 时间门控
               (盘后端到端验证用; 生产调度不要开)
    """
    logger.info('===== intraday_loop 启动 {} max_rounds={} force={} =====',
                datetime.now(), max_rounds, force)
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
    _SECTOR_FLOW_HISTORY.clear()  # 重启清空板块资金流历史 (跨进程累积从本轮开始)
    try:
        while True:
            now = datetime.now()
            if not force:
                # 退出条件: 非交易日 或 15:00 后
                if not is_trading_day(now):
                    logger.info('非交易日, 退出主循环')
                    break
                if now.time() >= dtime(15, 0):
                    logger.info('15:00 后, 退出主循环')
                    break
                # 14:57 退出让位给收盘竞价 auction_monitor (避免争 COM)
                if now.time() >= dtime(14, 57):
                    logger.info('14:57 收盘竞价, 退出让位给 auction_monitor')
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
                # C8 拆表: 读快照+intraday 两表, 按 code merge 后传给 intraday_engine
                snap = query_df(con, f"SELECT * FROM qd_stock_snapshot "
                                    f"WHERE snapshot_time > '{cutoff(seconds=30)}'")
                intra_snap = query_df(con, f"SELECT * FROM qd_stock_intraday "
                                     f"WHERE snapshot_time > '{cutoff(seconds=30)}'")
                snap = _merge_intraday(snap, intra_snap)
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
                # k5 合成当天 K (get_market_data 只给历史, 当天 K 必须本地合成;
                # 在 k1 之前, 让 k1 读到今天的 5m K)
                try:
                    k5.run(con=con)
                except Exception as e:
                    logger.error('k5 合成失败: {}', e)
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
                # 大单检测 → qd_big_order + 刷新 ctx.big_order_df (须在策略遍历前, 供 p12)
                _run_big_order(con, ctx)
                # 个股明暗资金 → qd_money_flow + 刷新 ctx.money_flow_df (须在策略遍历前, 供 p08/p12)
                _run_money_flow(con, ctx)
                # 板块资金流 (前移到策略遍历前; 供 _run_rotation → ctx.rotation_signal 给 p05)
                sector_agg_now = _run_sector_flow(con, ctx)
                # 板块轮动检测 → ctx.rotation_signal (依赖 ≥2 期历史)
                rot_sig = _run_rotation(sector_agg_now)
                if rot_sig is not None:
                    ctx.rotation_signal = rot_sig
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

                # k4 深度情绪 (5min/轮: 每 5 次 60s 块跑一次)
                _k4_deep_round = getattr(_run_rotation, '_k4_deep_round', 0) + 1
                _run_rotation._k4_deep_round = _k4_deep_round
                if _k4_deep_round % 5 == 0:
                    try:
                        k4_deep = k4.run(con, ctx)
                        ctx.sentiment_deep = k4_deep
                    except Exception as e:
                        logger.error('k4 深度情绪失败: {}', e)

                    # k4 板块热力图 + 梯队 (同 5min 周期)
                    try:
                        k4_heatmap.run(con, ctx)
                    except Exception as e:
                        logger.error('k4 板块热力图失败: {}', e)

                    # k4 打板梯队 (同 5min 周期)
                    try:
                        k4_ladder.run(con, ctx)
                    except Exception as e:
                        logger.error('k4 打板梯队失败: {}', e)
                # 共振分析 (先于策略评估, 确保 ctx.resonance_df 为本轮数据)
                _run_resonance(con, ctx)
                # 因子引擎: AlphaEngine → alpha_df (挂 ctx 供 p20-p26 消费)
                try:
                    from compute.alpha_engine import AlphaEngine
                    if _alpha_engine is None:
                        _alpha_engine = AlphaEngine.from_yaml(_YAML_PATH)
                    if _alpha_engine is not None:
                        alpha_df, coverage = _alpha_engine.compute(ctx)
                        ctx.alpha_df = alpha_df
                        if coverage > 0:
                            from compute.ranking import rank_sector_neutral
                            ctx.top_candidates = rank_sector_neutral(alpha_df, top_n=50)
                except Exception as e:
                    logger.error('AlphaEngine 失败: {}', e)
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
                # 止损止盈出场检查 (遍历持仓)
                for pos in list(risk.positions):
                    try:
                        price = _safe_float(pos.get('current_price', pos.get('cost_price')))
                        if price <= 0:
                            from lib.qdb import query_one
                            row = query_one(con,
                                "SELECT Now FROM qd_pricevol WHERE code = %s "
                                "ORDER BY snapshot_time DESC LIMIT 1",
                                (pos.get('code', ''),))
                            if row:
                                price = _safe_float(row.get('Now'))
                        exit_signal = risk.check_exit(pos, price)
                        if exit_signal:
                            action, reason = exit_signal
                            decisions.append(Decision(
                                action='sell', code=pos.get('code', ''),
                                strategy='risk_manager',
                                reason=reason, price=price, score=80.0,
                            ))
                    except Exception as e:
                        logger.warning('止损止盈检查失败 {}: {}', pos.get('code'), e)

                # 风控 + 飞书推送
                _process_decisions(con, decisions, risk, ctx)

            # 心跳: 主循环每轮刷新时间戳
            _write_heartbeat('intraday_loop')

            # 轮次计数放在最后 (确保 k4%6 在本轮开始时正确判定)
            round_idx += 1

            # 盘后验证: 跑完 max_rounds 轮退出
            if max_rounds is not None and round_idx >= max_rounds:
                logger.info('EOD 验证: 跑完 {} 轮, 退出', max_rounds)
                break

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
    import signal

    def _graceful_exit(signum, frame):
        raise KeyboardInterrupt

    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    init()
    try:
        # 盘后端到端验证: EOD_FORCE=1 EOD_MAX_ROUNDS=1 python runner/intraday_loop.py
        max_rounds = int(os.environ.get('EOD_MAX_ROUNDS', '0')) or None
        force = bool(os.environ.get('EOD_FORCE'))
        run(max_rounds=max_rounds, force=force)
    finally:
        close()


if __name__ == '__main__':
    main()
