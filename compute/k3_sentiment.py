"""k3: 大盘情绪监控

脚本路径: K:\QuestDB_test\\compute\\k3_sentiment.py
用途: 三层情绪分析 (大盘层定仓位 / 板块层定方向 / 个股层6池定标的), 写 qd_sentiment_*
数据源: ctx.pricevol_df (全场涨跌) + ctx.snapshot_focus_df (涨停/封单/连板) +
        ctx.index_snapshot (主指数) + relation_graph (板块映射)
入库表: qd_sentiment_snapshot_min / qd_sentiment_event_log
频率: 60s/轮 (跟 60s 块)
算法移植: DB数据库_v2 00_大盘情绪监控 (rate_emotion/detect_divergence/check_turn/6池)

核心算法:
  - rate_emotion: 4 分量 (涨停数/封板率/最高连板/涨跌比) 各评 1 档取最差档 → 5 档
  - detect_divergence: 指数涨但涨跌比低(价宽背离); 价资/价量/北向/期指 本次 stub
  - check_turn: 跨帧 (FrameState 近 20 帧) 变盘检测 (涨停骤降/涨跌比翻转/情绪跨越)
  - build_pools: 6 池 (连板/首板/龙头/炸板/易炸/A杀)

口径说明:
  - zt_cnt/fbl/max_lb 基于 focus 池 (qd_stock_snapshot, c2+c3 intraday), 非全市场;
    udr/up_cnt/down_cnt 基于 qd_pricevol 全场。阈值 TH.EMOTION_* 按 focus 池校准,
    实盘运行后据 focus 池大小调整。
  - snapshot 双形态行 (c2@T 快照 + c3@T+1s intraday) 用 _merge_dual_rows 合并取非空。
"""

import os
import sys
import json
from collections import deque
from dataclasses import dataclass

import pandas as pd
import numpy as np

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, executemany_batch  # noqa: E402

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k3_sentiment_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

DST_MIN = 'qd_sentiment_snapshot_min'
DST_EVENT = 'qd_sentiment_event_log'

_MIN_COLS = ['snapshot_time', 'emotion', 'emotion_order', 'zt_cnt', 'dt_cnt',
             'break_cnt', 'fbl', 'max_lb', 'udr', 'up_cnt', 'down_cnt',
             'index_zaf', 'top_sectors', 'lb_tier']
_EVENT_COLS = ['event_time', 'event_type', 'description', 'detail']


@dataclass(frozen=True)
class TH:
    """阈值集中管理 (调阈值不改业务代码)"""
    # 情绪评级 4 分量分档 (各分量按阈值落档, 4 档取最差档 = 保守)
    EMOTION_ZT = (30, 60, 100, 150)       # 涨停数 → 冰点/低迷/中性/活跃/过热
    EMOTION_FBL = (60.0, 70.0, 80.0, 90.0)   # 封板率 %
    EMOTION_LB = (3, 4, 6, 9)             # 最高连板
    EMOTION_UDR = (0.5, 1.0, 2.0, 3.0)    # 涨跌比
    # 背离
    DIV_INDEX_MIN_ZAF = 0.3    # 指数涨 > 0.3% 才判背离
    DIV_PRICE_WIDTH_UDR = 0.8  # 涨跌比 < 0.8 = 价宽背离 (二八虚涨)
    # 变盘 (跨帧对比 TURN_FRAMES 帧 ≈ TURN_FRAMES 分钟)
    TURN_ZT_DROP_PCT = 30.0    # 涨停数降幅 > 30%
    TURN_UDR_HIGH = 1.5
    TURN_UDR_LOW = 0.8
    TURN_FRAMES = 5
    # 涨跌停判定 (FCAmo=封单额)
    SEAL_AMO_POS = 0           # FCAmo > 0 → 涨停
    NEAR_ZT_RATIO = 0.999      # Max >= ZTPrice * 0.999 → 曾触涨停
    # 6池
    LEADER_AMO_MIN = 1e7       # 龙头封单额 >= 1000 万
    EASY_BREAK_FCB = 0.1       # 易炸: 封成比 < 0.1
    EASY_BREAK_AMO = 5e6       # 易炸: 封单额 < 500 万
    A_SHA_DROP_PCT = -5.0      # A杀: 昨涨停今跌 > 5%


EMOTION_ORDER = {'冰点': 0, '低迷': 1, '中性': 2, '活跃': 3, '过热': 4}
EMOTION_LABELS = ['冰点', '低迷', '中性', '活跃', '过热']


def _safe_float(v, default=0.0):
    try:
        r = float(v)
        if r != r:  # NaN (tqcenter 偶尔返回 nan, int(nan) 会抛 ValueError)
            return default
        return r
    except (TypeError, ValueError):
        return default


def _bin(value, bins):
    """按阈值落档: bins=(b0,b1,b2,b3) → 0..4 (value<b0=0档, >=b3=4档)"""
    for i, b in enumerate(bins):
        if value < b:
            return i
    return len(bins)


def classify_stock(fcamo, mx, zt_price):
    """涨跌停判定 (DB文档权威): FCAmo>0涨停 / <0跌停 / =0且Max>=ZTPrice炸板"""
    fcamo = _safe_float(fcamo)
    if fcamo > TH.SEAL_AMO_POS:
        return 'zt'
    if fcamo < TH.SEAL_AMO_POS:
        return 'dt'
    mx = _safe_float(mx)
    zt = _safe_float(zt_price)
    if zt > 0 and mx >= zt * TH.NEAR_ZT_RATIO:
        return 'break'
    return 'normal'


def rate_emotion(zt_cnt, fbl, max_lb, udr):
    """4 分量各评 1 档取最差档 → (label, order)"""
    orders = [
        _bin(zt_cnt, TH.EMOTION_ZT),
        _bin(fbl, TH.EMOTION_FBL),
        _bin(max_lb, TH.EMOTION_LB),
        _bin(udr, TH.EMOTION_UDR),
    ]
    worst = min(orders)  # 保守: 取最差档
    return EMOTION_LABELS[worst], worst


def _calc_market_breadth(pricevol_df):
    """全场涨跌家数 + 涨跌比 (udr) — pricevol 全场口径 (向量化)"""
    if pricevol_df is None or pricevol_df.empty:
        return 0, 0, 0.0
    df = pricevol_df
    if 'snapshot_time' in df.columns:
        df = df.sort_values('snapshot_time').groupby('code', as_index=False).last()
    else:
        df = df.groupby('code', as_index=False).last()
    lc = pd.to_numeric(df['LastClose'], errors='coerce')
    nw = pd.to_numeric(df['Now'], errors='coerce')
    valid = lc > 0
    up = int(((nw > lc) & valid).sum())
    down = int(((nw < lc) & valid).sum())
    udr = (up / down) if down > 0 else (float('inf') if up > 0 else 1.0)
    udr = min(udr, 99.0)
    return up, down, udr


def _calc_seal_stats(merged_df, _cls=None, lb_series=None):
    """从合并后的 snapshot 算 涨停/跌停/炸板数 + 封板率 + 最高连板 (向量化)"""
    if merged_df is None or merged_df.empty:
        return 0, 0, 0, 0.0, 0
    if _cls is None:
        fcamo = pd.to_numeric(merged_df['FCAmo'], errors='coerce').fillna(0)
        mx = pd.to_numeric(merged_df['Max'], errors='coerce').fillna(0)
        ztp = pd.to_numeric(merged_df['ZTPrice'], errors='coerce').fillna(0)
        _cls = np.select(
            [fcamo > 0, fcamo < 0, (fcamo == 0) & (mx >= ztp * 0.999)],
            ['zt', 'dt', 'break'], default='normal')
    if lb_series is None:
        lb_series = pd.to_numeric(
            merged_df['fLianB'].fillna(merged_df['EverZTCount']),
            errors='coerce').fillna(0).astype(int)
    zt = int((_cls == 'zt').sum())
    dt = int((_cls == 'dt').sum())
    brk = int((_cls == 'break').sum())
    max_lb = int(lb_series.max())
    sealed = zt + brk
    fbl = (zt / sealed * 100) if sealed > 0 else 0.0
    return zt, dt, brk, fbl, max_lb


def build_pools(merged_df, cls_arr=None, lb_arr=None,
                fcamo_arr=None, fcb_arr=None, chg_arr=None):
    """6 池分类: 连板/首板/龙头/炸板/易炸/A杀 → dict[code_list] (向量化)"""
    pools = {'lianban': [], 'shouban': [], 'leader': [], 'break': [],
             'easy_break': [], 'a_sha': []}
    if merged_df is None or merged_df.empty:
        return pools
    # 预计算向量列 (若调用方未提供)
    codes = merged_df['code'].tolist()
    if cls_arr is None:
        fcamo_v = pd.to_numeric(merged_df['FCAmo'], errors='coerce').fillna(0)
        mx_v = pd.to_numeric(merged_df['Max'], errors='coerce').fillna(0)
        ztp_v = pd.to_numeric(merged_df['ZTPrice'], errors='coerce').fillna(0)
        cls_arr = np.select([fcamo_v > 0, fcamo_v < 0, (fcamo_v == 0) & (mx_v >= ztp_v * 0.999)],
                            ['zt', 'dt', 'break'], default='normal')
    else:
        fcamo_v = fcamo_arr
    if lb_arr is None:
        lb_arr = pd.to_numeric(merged_df['fLianB'].fillna(merged_df['EverZTCount']),
                               errors='coerce').fillna(0).astype(int)
    if fcb_arr is None:
        fcb_arr = pd.to_numeric(merged_df['FCb'], errors='coerce').fillna(0)
    if fcamo_arr is None:
        fcamo_arr = pd.to_numeric(merged_df['FCAmo'], errors='coerce').fillna(0)
    if chg_arr is None:
        nw_v = pd.to_numeric(merged_df['Now'], errors='coerce').fillna(0)
        lc_v = pd.to_numeric(merged_df['LastClose'], errors='coerce').fillna(0)
        chg_arr = np.where(lc_v > 0, (nw_v - lc_v) / lc_v * 100, 0.0)

    last_start_zt = merged_df['LastStartZT'].fillna('').astype(bool)

    # 布尔掩码 → code 列表
    mask_lb2 = lb_arr >= 2
    mask_lb1 = lb_arr == 1
    pools['lianban'] = [c for c, m in zip(codes, mask_lb2) if m]
    pools['shouban'] = [c for c, m in zip(codes, mask_lb1) if m]

    leader_mask = mask_lb2 & (fcamo_arr >= TH.LEADER_AMO_MIN)
    if leader_mask.any():
        leader_df = merged_df[leader_mask].copy()
        leader_df['_fcamo'] = fcamo_arr[leader_mask]
        pools['leader'] = leader_df.sort_values('_fcamo', ascending=False)['code'].head(20).tolist()

    break_mask = cls_arr == 'break'
    pools['break'] = [c for c, m in zip(codes, break_mask) if m]

    easy_break_mask = (cls_arr == 'zt') & ((fcb_arr < TH.EASY_BREAK_FCB) | (fcamo_arr < TH.EASY_BREAK_AMO))
    pools['easy_break'] = [c for c, m in zip(codes, easy_break_mask) if m]

    a_sha_mask = last_start_zt & ((cls_arr == 'dt') | (chg_arr <= TH.A_SHA_DROP_PCT))
    pools['a_sha'] = [c for c, m in zip(codes, a_sha_mask) if m]
    return pools


def build_sector_strength(merged_df, cls_arr=None, zaf_arr=None, lb_arr=None, zjl_arr=None):
    """板块强度: 每板块涨停数*3 + 涨幅 + 主力(归一) + 2板数*2 → Top 板块列表 (部分向量化)"""
    if merged_df is None or merged_df.empty:
        return []
    from lib.relation_graph import get_stock_sectors

    if cls_arr is None:
        fcamo_v = pd.to_numeric(merged_df['FCAmo'], errors='coerce').fillna(0)
        mx_v = pd.to_numeric(merged_df['Max'], errors='coerce').fillna(0)
        ztp_v = pd.to_numeric(merged_df['ZTPrice'], errors='coerce').fillna(0)
        cls_arr = np.select([fcamo_v > 0, fcamo_v < 0, (fcamo_v == 0) & (mx_v >= ztp_v * 0.999)],
                            ['zt', 'dt', 'break'], default='normal')
    if zaf_arr is None:
        zaf_arr = pd.to_numeric(merged_df['ZAF'], errors='coerce').fillna(0)
    if lb_arr is None:
        lb_arr = pd.to_numeric(merged_df['fLianB'].fillna(merged_df['EverZTCount']),
                               errors='coerce').fillna(0).astype(int)
    if zjl_arr is None:
        zjl_arr = pd.to_numeric(merged_df['Zjl'], errors='coerce').fillna(0)

    codes = merged_df['code'].tolist()
    agg = {}
    for i, code in enumerate(codes):
        if not code:
            continue
        sectors = get_stock_sectors(code) or []
        for s in sectors:
            bc = s.get('block_code')
            if not bc:
                continue
            a = agg.setdefault(bc, {'name': s.get('block_name', bc),
                                    'zt': 0, 'zaf_sum': 0.0, 'lb2': 0, 'flow': 0.0})
            if cls_arr[i] == 'zt':
                a['zt'] += 1
            a['zaf_sum'] += zaf_arr[i]
            if lb_arr[i] >= 2:
                a['lb2'] += 1
            a['flow'] += zjl_arr[i]
    scored = []
    for bc, a in agg.items():
        score = a['zt'] * 3 + a['zaf_sum'] + (1 if a['flow'] > 0 else 0) + a['lb2'] * 2
        scored.append({'code': bc, 'name': a['name'], 'score': round(score, 1),
                       'zt': a['zt'], 'lb2': a['lb2']})
    scored.sort(key=lambda x: -x['score'])
    return scored[:10]


def detect_divergence(index_zaf, udr, up_cnt, down_cnt):
    """指数层背离 (本次实现价宽; 价资/价量/北向/期指 stub)"""
    divs = []
    if index_zaf > TH.DIV_INDEX_MIN_ZAF and udr < TH.DIV_PRICE_WIDTH_UDR:
        divs.append({'type': 'price_width', 'desc': f'指数涨{index_zaf:.2f}%但涨跌比{udr:.2f}(二八虚涨)'})
    # TODO(批2后续): 价资背离(指数涨但主力净流出, 需 qd_index_snapshot Zjl)
    # TODO: 价量背离(涨但成交较昨缩>20%, 需 CJJEPre1)
    # TODO: 北向/期指 (数据源 2024 后停披露, 暂不实现)
    return divs


class FrameState:
    """进程内跨帧状态 (daemon 常驻, 近 N 帧)"""

    def __init__(self, max_frames=20):
        self.frames = deque(maxlen=max_frames)

    def add(self, frame):
        self.frames.append(frame)

    def prev_n(self, n):
        if len(self.frames) > n:
            return self.frames[-n - 1]
        return None


# 模块级单例 (intraday_loop 进程内常驻)
_STATE = FrameState(max_frames=20)


def check_turn(frame):
    """跨帧变盘检测 (对比 TURN_FRAMES 帧前)"""
    events = []
    prev = _STATE.prev_n(TH.TURN_FRAMES)
    if prev is None:
        return events
    # 涨停骤降
    if prev['zt_cnt'] > 0:
        drop = (prev['zt_cnt'] - frame['zt_cnt']) / prev['zt_cnt'] * 100
        if drop >= TH.TURN_ZT_DROP_PCT:
            events.append({'event_type': 'turn_zt_drop',
                           'description': f"涨停数骤降 {prev['zt_cnt']}→{frame['zt_cnt']} (-{drop:.0f}%)",
                           'detail': json.dumps({'prev': prev['zt_cnt'], 'cur': frame['zt_cnt']})})
    # 涨跌比翻转
    if prev['udr'] >= TH.TURN_UDR_HIGH and frame['udr'] <= TH.TURN_UDR_LOW:
        events.append({'event_type': 'turn_udr_flip',
                       'description': f"涨跌比翻转 {prev['udr']:.2f}→{frame['udr']:.2f}",
                       'detail': json.dumps({'prev': prev['udr'], 'cur': frame['udr']})})
    # 情绪跨越转弱
    if frame['emotion_order'] < prev['emotion_order']:
        events.append({'event_type': 'emotion_crossing',
                       'description': f"情绪转弱 {prev['emotion']}→{frame['emotion']}",
                       'detail': json.dumps({'prev': prev['emotion'], 'cur': frame['emotion']})})
    return events


def _main_index_zaf(index_snapshot):
    """主指数 (上证 000001.SH) 涨幅 %"""
    if not index_snapshot:
        return 0.0
    for code in ('000001.SH', '1A0001', 'sh000001'):
        s = index_snapshot.get(code)
        if s:
            now = _safe_float(s.get('Now'))
            lc = _safe_float(s.get('LastClose'))
            if lc > 0:
                return (now - lc) / lc * 100
    # 退化: 取第一个指数
    for s in index_snapshot.values():
        now = _safe_float(s.get('Now'))
        lc = _safe_float(s.get('LastClose'))
        if lc > 0:
            return (now - lc) / lc * 100
    return 0.0


def run(con, ctx):
    """情绪监控主流程 (60s/轮): 算评级→写库→返回 sentiment dict 挂 ctx

    Returns:
        dict: emotion/emotion_order/zt_cnt/udr/divergences/pools/top_sectors/events
    """
    from datetime import datetime
    now = datetime.now()

    # 1. 全场涨跌 (pricevol)
    up_cnt, down_cnt, udr = _calc_market_breadth(ctx.pricevol_df)

    # 2. 涨停/封板/连板 (snapshot_focus, C8 拆表后已合并完整, 取每 code 最新一行)
    _sdf = ctx.snapshot_focus_df
    if _sdf is not None and not _sdf.empty and 'snapshot_time' in _sdf.columns:
        merged = _sdf.sort_values('snapshot_time').groupby('code', as_index=False).last()
    else:
        merged = _sdf

    # 预计算向量列 (一次计算, 3 个函数复用, 避免 iterrows + 重复 classify_stock)
    _cls = None
    _lb_series = None
    _fcamo_arr = None
    _fcb_arr = None
    _chg_arr = None
    _zaf_arr = None
    _zjl_arr = None
    if merged is not None and not merged.empty:
        _fcamo_arr = pd.to_numeric(merged['FCAmo'], errors='coerce').fillna(0)
        mx_v = pd.to_numeric(merged['Max'], errors='coerce').fillna(0)
        ztp_v = pd.to_numeric(merged['ZTPrice'], errors='coerce').fillna(0)
        _cls = np.select([_fcamo_arr > 0, _fcamo_arr < 0,
                          (_fcamo_arr == 0) & (mx_v >= ztp_v * 0.999)],
                         ['zt', 'dt', 'break'], default='normal')
        _lb_series = pd.to_numeric(
            merged['fLianB'].fillna(merged['EverZTCount']),
            errors='coerce').fillna(0).astype(int)
        _fcb_arr = pd.to_numeric(merged['FCb'], errors='coerce').fillna(0)
        nw_v = pd.to_numeric(merged['Now'], errors='coerce').fillna(0)
        lc_v = pd.to_numeric(merged['LastClose'], errors='coerce').fillna(0)
        _chg_arr = np.where(lc_v > 0, (nw_v - lc_v) / lc_v * 100, 0.0)
        _zaf_arr = pd.to_numeric(merged['ZAF'], errors='coerce').fillna(0)
        _zjl_arr = pd.to_numeric(merged['Zjl'], errors='coerce').fillna(0)

    zt_cnt, dt_cnt, break_cnt, fbl, max_lb = _calc_seal_stats(merged, _cls, _lb_series)
    pools = build_pools(merged, _cls, _lb_series, _fcamo_arr, _fcb_arr, _chg_arr)

    # 3. 板块强度
    top_sectors = build_sector_strength(merged, _cls, _zaf_arr, _lb_series, _zjl_arr)

    # 4. 主指数涨幅
    index_zaf = _main_index_zaf(ctx.index_snapshot)

    # 5. 情绪评级 (4 分量取最差档)
    emotion, emotion_order = rate_emotion(zt_cnt, fbl, max_lb, udr)

    # 6. 背离
    divergences = detect_divergence(index_zaf, udr, up_cnt, down_cnt)

    # 7. 连板梯队 (按板数分层)
    lb_tier = {'lb2': pools['lianban'][:20], 'shouban': pools['shouban'][:20],
               'leader': pools['leader']}

    # 8. 帧 → 跨帧变盘
    frame = {'ts': now, 'zt_cnt': zt_cnt, 'udr': udr, 'fbl': fbl,
             'max_lb': max_lb, 'emotion': emotion, 'emotion_order': emotion_order}
    events = check_turn(frame)
    _STATE.add(frame)

    # 9. 写 snapshot_min (分钟对齐时间戳, 同分钟去重)
    snap_time = now.replace(second=0, microsecond=0)
    snap_row = (snap_time, emotion, emotion_order, zt_cnt, dt_cnt, break_cnt,
                fbl, max_lb, udr, up_cnt, down_cnt, index_zaf,
                json.dumps(top_sectors, ensure_ascii=False),
                json.dumps(lb_tier, ensure_ascii=False))
    try:
        executemany_batch(con, DST_MIN, _MIN_COLS, [snap_row])
    except Exception as e:
        logger.warning('写 qd_sentiment_snapshot_min 失败: {}', e)

    # 10. 写变盘事件 + 推飞书
    if events:
        evt_rows = [(now, e['event_type'], e['description'], e['detail']) for e in events]
        try:
            executemany_batch(con, DST_EVENT, _EVENT_COLS, evt_rows)
        except Exception as e:
            logger.warning('写 qd_sentiment_event_log 失败: {}', e)
        try:
            from feishu import push_text
            lines = [f'⚠️ 变盘预警 {now.strftime("%H:%M")}']
            for e in events:
                lines.append(f'  · {e["description"]}')
            push_text('\n'.join(lines))
        except Exception as e:
            logger.warning('情绪变盘飞书推送失败: {}', e)

    logger.info('k3 情绪: {} (zt={} fbl={:.0f}% lb={} udr={:.2f}) 背离={} 变盘={}',
                emotion, zt_cnt, fbl, max_lb, udr, len(divergences), len(events))

    return {
        'emotion': emotion,
        'emotion_order': emotion_order,
        'zt_cnt': zt_cnt,
        'dt_cnt': dt_cnt,
        'break_cnt': break_cnt,
        'fbl': fbl,
        'max_lb': max_lb,
        'udr': udr,
        'index_zaf': index_zaf,
        'divergences': divergences,
        'pools': pools,
        'top_sectors': top_sectors,
        'events': events,
    }


if __name__ == '__main__':
    # 独立运行: 用 mock ctx 测试算法
    from lib.qdb import connect
    from strategy.context import StrategyContext
    con = connect()
    ctx = StrategyContext(timestamp=__import__('datetime').datetime.now(), is_trading=True)
    try:
        result = run(con, ctx)
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ('pools', 'top_sectors')},
                         ensure_ascii=False, indent=2, default=str))
    finally:
        con.close()
