"""k4: 打板梯队 + 2进3 晋级监控

脚本路径: K:\QuestDB_test\\compute\\k4_ladder_tracker.py
用途:
  1. 连板全景 — 按板数分组显示全部连板股票
  2. 2进3 重点 — 今日 2 连板晋级概率评分 Top 5
  3. 板块共振 — 2进3 标的对应板块强度
数据源: qd_stock_snapshot + qd_stock_daily + qd_stock_gpjy + qd_sector_flow
写入表: qd_ladder_tracker (5min/轮)
推送: 飞书推送 (队列变更时触发)
频率: 5min/轮 (同 k4 深度情绪 + 板块热力图)

连板数算法:
  - qd_stock_daily.ConZAFDateNum = 连续涨停天数 (含昨日)
  - 今日再涨停 (FCAmo > 0) → 实际连板 = ConZAFDateNum (延续昨日)
  - 今日未涨停 → 连板断裂, 不计入梯队
  注: 如果当前是首板 (昨日未涨停), ConZAFDateNum 可能为 0, 但今日 FCAmo > 0 → 手动设为 1

2进3 评分维度 (各维度加权求和, 满分 100):
  1. FCAmo 封单额 30%    - 封板决心
  2. FCb 封成比 15%      - 封板质量
  3. gp40_lb_rate 连板率 20% - 历史股性
  4. gp39_next_red_rate 10%  - T+1 溢价
  5. fHSL 换手率 10%    - 筹码健康度 (最优区间 5-15%)
  6. sector_score 板块强度 10% - 板块共振
  7. gp14_break_cnt 开板次数 5% - 封板稳定性 (负分)
"""

import os
import sys
import json
import time
from datetime import datetime

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
from lib.qdb import connect, query_df, executemany_batch, cutoff
from lib.relation_graph import get_stock_sectors, get_sector_raw_type, _sector_meta

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k4_ladder_tracker_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

DST = 'qd_ladder_tracker'
_COLS = [
    'snapshot_time',
    'lb_tiers', 'promotion_rankings', 'sector_resonance', 'stats',
    'calc_duration_ms',
]

# 2进3 评分权重 (知识库规范: FCb/OpenAmo/OpenZTBuy/fHSL/fLianB)
_W = {
    'fcb': 0.30,
    'open_amo': 0.25,
    'open_zt_buy': 0.20,
    'hsl': 0.15,
    'flianb': 0.10,
}

_HEALTH_LABELS = [
    (75, '🟢健康'),
    (50, '🟡一般'),
]


def _sf(v, default=0.0):
    try:
        r = float(v)
        if r != r:
            return default
        return r
    except (TypeError, ValueError):
        return default


def _health_label(score):
    for threshold, label in _HEALTH_LABELS:
        if score >= threshold:
            return label
    return '🔴危险'


def _safe_name(code):
    """从名称映射取股票名称"""
    from lib.relation_graph import get_stock_name
    return get_stock_name(code)


# ══════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════

def _load_snapshot_data(con):
    """读个股快照: 分别从 qd_stock_snapshot + qd_stock_intraday 取数据, 按 code 合并

    C8 拆表后: FCAmo/FCb/fHSL/fLianB 在 qd_stock_intraday 表,
    Now/LastClose 在 qd_stock_snapshot 表。
    c2/c3 的时间戳不完全对齐, 按 code 取最新而不是精确 timestamp JOIN。

    返回:
      dict[stock_code, {fcamo, fcb, hsl, zaf, sector}]
    """
    try:
        from datetime import timedelta
        ts = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S')

        # 分别读两表, 每个 code 取最新
        snap = query_df(con,
            f"SELECT code, Now, LastClose, snapshot_time FROM qd_stock_snapshot "
            f"WHERE snapshot_time > '{ts}'")
        intra = query_df(con,
            f"SELECT code, snapshot_time, "
            f"  Cast(FCAmo AS DOUBLE) AS fcamo, Cast(FCb AS DOUBLE) AS fcb, "
            f"  Cast(fHSL AS DOUBLE) AS fhsl, Cast(fLianB AS DOUBLE) AS flianb "
            f"FROM qd_stock_intraday "
            f"WHERE snapshot_time > '{ts}'")

        if snap is None or snap.empty:
            return {}

        snap_l = snap.sort_values('snapshot_time').groupby('code', as_index=False).last()

        if intra is not None and not intra.empty:
            intra_l = intra.sort_values('snapshot_time').groupby('code', as_index=False).last()
            # 按 code 左合并 (保留全部快照行, intraday 缺的填 NaN)
            merged = snap_l.merge(intra_l, on='code', how='left', suffixes=('', '_i'))
        else:
            merged = snap_l

        result = {}
        for _, r in merged.iterrows():
            code = r['code']
            now = _sf(r.get('Now'))
            lc = _sf(r.get('LastClose'))
            zaf = round((now - lc) / lc * 100, 2) if lc > 0 else 0.0
            result[code] = {
                'fcamo': _sf(r.get('fcamo')),
                'fcb': _sf(r.get('fcb')),
                'hsl': _sf(r.get('fhsl')),
                'zaf': zaf,
                'flianb': _sf(r.get('flianb')),
            }
        return result
    except Exception as e:
        logger.warning('读快照失败: {}', e)
        return {}


def _load_daily_data(con):
    """读日级数据: 最近一天每 code 的 LastZTHzNum, LastStartZT, EverZTCount, OpenAmo, OpenZTBuy

    返回:
      dict[stock_code, {last_zthz, last_start_zt, ever_zt, open_amo, open_zt_buy}]
    """
    try:
        df = query_df(con,
            "SELECT code, date, LastZTHzNum, LastStartZT, EverZTCount, OpenAmo, OpenZTBuy "
            "FROM qd_stock_daily "
            f"WHERE date > '{cutoff(days=2)}' "
            "ORDER BY date DESC")
        if df is None or df.empty:
            return {}
        latest = df.groupby('code', as_index=False).first()
        result = {}
        for _, r in latest.iterrows():
            result[r['code']] = {
                'last_zthz': int(_sf(r.get('LastZTHzNum'))),
                'last_start_zt': str(r.get('LastStartZT', '')),
                'ever_zt': int(_sf(r.get('EverZTCount'))),
                'open_amo': _sf(r.get('OpenAmo')),
                'open_zt_buy': _sf(r.get('OpenZTBuy')),
            }
        return result
    except Exception as e:
        logger.warning('读日级数据失败: {}', e)
        return {}


def _load_gp_data(con):
    """读 GP 股性数据: 每 code 最新 gp40_lb_rate/gp39_next_red_rate/gp14_break_cnt

    返回:
      dict[stock_code, {lb_rate, red_rate, break_cnt}]
    """
    try:
        df = query_df(con,
            "SELECT code, date, gp40_lb_rate, gp39_next_red_rate, gp14_break_cnt "
            "FROM qd_stock_gpjy "
            f"WHERE date > '{cutoff(days=30)}' "
            "ORDER BY date DESC")
        if df is None or df.empty:
            return {}
        latest = df.groupby('code', as_index=False).first()
        result = {}
        for _, r in latest.iterrows():
            result[r['code']] = {
                'lb_rate': _sf(r.get('gp40_lb_rate')),
                'red_rate': _sf(r.get('gp39_next_red_rate')),
                'break_cnt': _sf(r.get('gp14_break_cnt')),
            }
        return result
    except Exception as e:
        logger.warning('读 GP 数据失败: {}', e)
        return {}


def _load_sector_flow(con):
    """读板块资金流: 最新帧每板块主力净流

    返回:
      dict[block_code, main_net]
    """
    try:
        df = query_df(con,
            f"SELECT code, main_net FROM qd_sector_flow "
            f"WHERE flow_time > '{cutoff(minutes=5)}'")
        if df is not None and not df.empty:
            latest = df.groupby('code', as_index=False).last()
            return {r['code']: _sf(r.get('main_net')) for _, r in latest.iterrows()}
    except Exception as e:
        logger.warning('读板块资金流失败: {}', e)
    return {}


# ══════════════════════════════════════════════════════════════
# 连板识别
# ══════════════════════════════════════════════════════════════

def _compute_board_count(stock_code, snap, daily):
    """计算个股当前连板数

    Args:
      stock_code: 股票代码
      snap: _load_snapshot_data 结果中该股的 dict 或 None
      daily: _load_daily_data 结果中该股的 dict 或 None

    算法 (知识库规范):
      - 今日必须涨停 (FCAmo > 0), 否则不计入梯队
      - 用 LastZTHzNum (几板) 判定连板数
      - LastZTHzNum > 0 → 延续连板，连板数 = LastZTHzNum
      - LastZTHzNum <= 0 且 FCAmo > 0 → 首板 (lb=1)

    Returns:
      int: 连板数 (0 = 未涨停或断裂)
    """
    if snap is None:
        return 0
    fcamo = _sf(snap.get('fcamo'))
    if fcamo <= 0:
        return 0  # 未涨停, 不计入

    last_zthz = daily.get('last_zthz', 0) if daily else 0
    if last_zthz > 0:
        return last_zthz  # 知识库规范: LastZTHzNum = 几板
    return 1  # 首板


def _get_stock_name(stock_code, snap):
    """查股票名称, 优先从名称映射取"""
    from lib.relation_graph import get_stock_name
    return get_stock_name(stock_code)


# ══════════════════════════════════════════════════════════════
# 2进3 评分
# ══════════════════════════════════════════════════════════════

def _score_2to3(stock_code, snap, daily, sector_flow):
    """对一只 2 连板股进行晋级概率评分 (知识库规范: FCb/OpenAmo/OpenZTBuy/fHSL/fLianB)

    Args:
      stock_code: 股票代码
      snap: 快照数据 dict (含 fcamo/fcb/hsl/flianb)
      daily: 日级数据 dict (含 open_amo/open_zt_buy)
      sector_flow: {block_code: main_net}

    Returns:
      (score, health, detail_dict)
    """
    fcb = _sf(snap.get('fcb'))
    hsl = _sf(snap.get('hsl'))
    flianb = _sf(snap.get('flianb'))
    fcamo = _sf(snap.get('fcamo'))

    open_amo = _sf(daily.get('open_amo')) if daily else 0.0
    open_zt_buy = _sf(daily.get('open_zt_buy')) if daily else 0.0

    # 板块共振分数 (取所属板块最高主力净流)
    sectors = get_stock_sectors(stock_code) or []
    sector_score = 0.0
    best_sector = ''
    for s in sectors:
        bc = s.get('block_code', '')
        mn = _sf(sector_flow.get(bc))
        if abs(mn) > abs(sector_score):
            sector_score = mn
            best_sector = bc
    sector_norm = _sf(_norm(min(sector_score, 1e9), -1e9, 1e9))

    # 各维度评分 [0, 1] (知识库规范: FCb/OpenAmo/OpenZTBuy/fHSL/fLianB)
    open_amo_norm = _norm(open_amo, 0, 5e6)    # 0-500万
    open_zt_buy_norm = _norm(open_zt_buy, 0, 2e6)  # 0-200万
    fcb_norm = _norm(fcb, 0, 0.5)             # 0-0.5
    flianb_norm = _norm(flianb, 0, 5)          # 0-5 量比
    hsl_norm = _hsl_score(hsl)                  # 0-1, 最优区间

    total = (
        fcb_norm * _W['fcb'] +
        open_amo_norm * _W['open_amo'] +
        open_zt_buy_norm * _W['open_zt_buy'] +
        hsl_norm * _W['hsl'] +
        flianb_norm * _W['flianb']
    ) * 100  # 缩放到 0-100

    total = round(max(0, min(100, total)), 1)
    health = _health_label(total)

    # FCb > 0.3 加分, FCb < 0.05 减分
    if fcb > 0.3:
        total = min(100, total + 10)
    elif fcb < 0.05:
        total = max(0, total - 15)

    detail = {
        'fcb_score': round(fcb_norm * _W['fcb'] * 100, 1),
        'open_amo_score': round(open_amo_norm * _W['open_amo'] * 100, 1),
        'open_zt_buy_score': round(open_zt_buy_norm * _W['open_zt_buy'] * 100, 1),
        'hsl_score': round(hsl_norm * _W['hsl'] * 100, 1),
        'flianb_score': round(flianb_norm * _W['flianb'] * 100, 1),
    }

    # FCAmo==0 标弱势 (知识库规范)
    if fcamo <= 0:
        health = '弱势'
        total = max(0, total - 30)

    return total, health, detail, best_sector


def _norm(v, lo, hi):
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _hsl_score(hsl):
    """换手率健康度评分, 最优 5-15%"""
    if 5 <= hsl <= 15:
        return 1.0
    if hsl < 5:
        return max(0, hsl / 5)
    # > 15%
    if hsl <= 25:
        return max(0, 1 - (hsl - 15) / 10)
    return 0.0


# ══════════════════════════════════════════════════════════════
# 主计算
# ══════════════════════════════════════════════════════════════

def _compute(con, snap_data, daily_data, gp_data, sector_flow):
    """综合计算连板全景 + 2进3 排行 + 板块共振

    Returns:
      dict: {lb_tiers, promotion_rankings, sector_resonance, stats}
    """
    tiers = {1: [], 2: [], 3: [], 4: [], '5+': []}
    candidates_2to3 = []

    for code, snap in (snap_data or {}).items():
        daily = (daily_data or {}).get(code)
        gp = (gp_data or {}).get(code)

        lb = _compute_board_count(code, snap, daily)
        if lb < 1:
            continue

        # 放入对应梯队 (5 板以上归入 '5+')
        tier_key = lb if lb <= 4 else '5+'
        entry = {
            'code': code,
            'name': _get_stock_name(code, snap),
            'fcamo': _sf(snap.get('fcamo')),
            'zaf': _sf(snap.get('zaf')),
            'fcb': _sf(snap.get('fcb')),
            'hsl': _sf(snap.get('hsl')),
        }
        tiers.setdefault(tier_key, []).append(entry)

        if lb == 2:
            score, health, detail, best_sector = _score_2to3(code, snap, daily_data.get(code), sector_flow)
            candidates_2to3.append({
                'code': code,
                'name': _get_stock_name(code, snap),
                'fcamo': _sf(snap.get('fcamo')),
                'fcb': _sf(snap.get('fcb')),
                'lb_rate': _sf(gp.get('lb_rate')) if gp else 0.0,
                'red_rate': _sf(gp.get('red_rate')) if gp else 0.0,
                'break_cnt': _sf(gp.get('break_cnt')) if gp else 0.0,
                'hsl': _sf(snap.get('hsl')),
                'sector_code': best_sector,
                'sector_name': _sector_meta.get(best_sector, {}).get('sector_name', '') if best_sector else '',
                'total_score': score,
                'health': health,
                'detail': detail,
            })

    # 全局最强 Top3: 按连板数降序→FCAmo降序 (知识库规范)
    flat_all = []
    for k, items in tiers.items():
        for item in items:
            flat_all.append((k, item))
    flat_all.sort(key=lambda x: (-x[0], -x[1]['fcamo']))
    top3 = flat_all[:3]

    # 2进3 晋级池: 全局排序 (含评分降序)
    candidates_2to3.sort(key=lambda x: -x['total_score'])

    # 统计数据
    stats = {
        'total_zt': sum(len(v) for v in tiers.values()),
        'total_1b': len(tiers.get(1, [])),
        'total_2b': len(tiers.get(2, [])),
        'total_3b': len(tiers.get(3, [])),
        'total_4b': len(tiers.get(4, [])),
        'total_5b_plus': len(tiers.get('5+', [])),
        'candidates_2to3': len(candidates_2to3),
    }

    # 板块共振: 2进3 标的所属板块强度
    sector_res = []
    for c in candidates_2to3:
        sc = c.get('sector_code', '')
        if sc:
            sector_res.append({
                'sector_code': sc,
                'sector_name': c.get('sector_name', ''),
                'main_net': _sf(sector_flow.get(sc)),
                'stock_code': c['code'],
                'stock_name': c['name'],
            })

    logger.info('连板梯队: 1={} 2={} 3={} 4={} 5+={} | 2进3候选={}',
                stats['total_1b'], stats['total_2b'], stats['total_3b'],
                stats['total_4b'], stats['total_5b_plus'], stats['candidates_2to3'])

    return {
        'lb_tiers': top3,                # 全局最强 Top3 (知识库规范)
        'promotion_rankings': candidates_2to3[:5],  # 2进3 Top5
        'sector_resonance': sector_res,
        'stats': stats,
    }


_TOP_N_CANDIDATES = 5


# ══════════════════════════════════════════════════════════════
# 推送
# ══════════════════════════════════════════════════════════════

def push_ladder(result):
    """推送打板梯队消息到飞书

    Args:
        result: run() 返回的 dict
    Returns:
        bool
    """
    try:

        lines = []
        ts = datetime.now().strftime('%H:%M')
        lines.append(f'──── 打板梯队 {ts} ────')
        lines.append('')

        # 连板全景
        stats = result.get('stats', {})
        lines.append('连板全景:')
        lines.append(f'  首板: {stats.get("total_1b", 0)}家  '
                     f'2板: {stats.get("total_2b", 0)}家  '
                     f'3板: {stats.get("total_3b", 0)}家  '
                     f'4板: {stats.get("total_4b", 0)}家  '
                     f'5板+: {stats.get("total_5b_plus", 0)}家')
        lines.append('')

        # 最强梯队 Top 3 (知识库规范: 全局前3)
        top3 = result.get('lb_tiers', [])
        lines.append('最强梯队 Top 3:')
        for i, entry in enumerate(top3[:3]):
            if isinstance(entry, tuple):
                s = entry[1] if len(entry) > 1 else {}
            elif isinstance(entry, dict):
                s = entry
            else:
                continue
            fcamo_yi = _sf(s.get('fcamo')) / 1e8
            mark = ''
            if fcamo_yi >= 0.5:
                mark = ' ⚡'
            elif fcamo_yi <= 0.05:
                mark = ' ⚠'
            lines.append(f'  {i+1}. {s.get("name", s.get("code",""))}  FCAmo{fcamo_yi:.2f}亿{mark}')
        lines.append('')

        # 2进3 重点
        candidates = result.get('promotion_rankings', [])
        if candidates:
            lines.append('──── 2进3 重点监控 ────')
            for c in candidates:
                health = c.get('health', '')
                fcamo_yi = _sf(c.get('fcamo')) / 1e8
                sector_str = f' 板块{c.get("sector_name","")}' if c.get('sector_name') else ''
                lines.append(
                    f'{health} {c.get("name","")}({c["code"]})  '
                    f'评分{c["total_score"]:.0f}  '
                    f'封单{fcamo_yi:.2f}亿{sector_str}'
                )
            lines.append('')

        # 板块共振
        sector_res = result.get('sector_resonance', [])
        seen_sectors = set()
        if sector_res:
            lines.append('板块共振:')
            for sr in sector_res:
                sname = sr.get('sector_name', '')
                if sname and sname not in seen_sectors:
                    seen_sectors.add(sname)
                    mn = _sf(sr.get('main_net'))
                    arrow = '🟢' if mn >= 0 else '🔴'
                    lines.append(f'  {arrow} {sname}  {mn/1e8:+.2f}亿')
            lines.append('')

        lines.append('─' * 22)
        lines.append('k4 打板梯队 | 5min 自动推送')

        text = '\n'.join(lines)
        return text
    except Exception as e:
        logger.warning('打板梯队推送失败: {}', e)
        return ''


# ══════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════

def _write_ladder(con, now, result, dur_ms):
    """写 qd_ladder_tracker 一行"""
    snap = now.replace(second=0, microsecond=0)

    def _j(obj):
        return json.dumps(obj, ensure_ascii=False)

    row = (
        snap,
        _j(result.get('lb_tiers', {})),
        _j(result.get('promotion_rankings', [])),
        _j(result.get('sector_resonance', [])),
        _j(result.get('stats', {})),
        dur_ms,
    )
    try:
        executemany_batch(con, DST, _COLS, [row])
        return True
    except Exception as e:
        logger.warning('写 qd_ladder_tracker 失败: {}', e)
        return False


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def run(con, ctx=None):
    """打板梯队主流程 (5min/轮)

    Args:
        con: psycopg2 连接
        ctx: StrategyContext (可选)

    Returns:
        dict: {lb_tiers, promotion_rankings, sector_resonance, stats}
    """
    t0 = time.time()
    now = datetime.now()
    logger.info('▶ k4 打板梯队计算开始')

    snap_data = _load_snapshot_data(con)
    daily_data = _load_daily_data(con)
    gp_data = _load_gp_data(con)
    sector_flow = _load_sector_flow(con)

    if not snap_data:
        logger.warning('打板梯队: 无快照数据, 跳过')
        return {}

    result = _compute(con, snap_data, daily_data, gp_data, sector_flow)
    dur_ms = int((time.time() - t0) * 1000)

    _write_ladder(con, now, result, dur_ms)

    stats = result.get('stats', {})
    logger.info('✓ k4 打板梯队完成 ({}ms): 连板={}家 2进3候选={}',
                dur_ms, stats.get('total_zt', 0), stats.get('candidates_2to3', 0))

    if ctx is not None:
        ctx.ladder_tracker = result

    return result


if __name__ == '__main__':
    logger.info('=== k4 打板梯队独立测试 ===')
    con = connect()
    try:
        r = run(con)
        stats = r.get('stats', {})
        print(f'\n连板: {stats}')
        tiers = r.get('lb_tiers', {})
        for k in ['5+', 4, 3, 2, 1]:
            v = tiers.get(k, [])
            if v:
                label = f'{k}板' if k != '5+' else '5板+'
                print(f'\n{label} ({len(v)}):')
                for s in v[:3]:
                    print(f'  {s.get("name","")}({s["code"]}) FCAmo{_sf(s["fcamo"])/1e8:.2f}亿')
        candidates = r.get('promotion_rankings', [])
        if candidates:
            print(f'\n2进3 排行:')
            for c in candidates:
                print(f'  {c["health"]} {c.get("name","")}({c["code"]}) '
                      f'评分{c["total_score"]:.0f} FCAmo{_sf(c["fcamo"])/1e8:.2f}亿')
    finally:
        con.close()
