"""k4: 深度大盘情绪分析

脚本路径: K:\QuestDB_test\\compute\\k4_sentiment.py
用途: 8 维度大盘情绪深度分析, 输出直观推送消息
数据源: QuestDB 已有表 (全读库, 不读 tqcenter)
写入表: qd_sentiment_deep (5min/轮)
推送: 飞书推送 (全市场全景 + 拐点提示)
频率: 5min/轮 (由 intraday_loop 每 60s 块的条件计数器触发)

k3 ↔ k4 边界:
  - k3: 60s/轮 实时评级 + 6池分类 + 跨帧变盘 → qd_sentiment_snapshot_min
  - k4: 5min/轮 综合读数, 推送直观「一眼看清」
  - k4 读取 k3 情绪评级和 k3 历史帧, 做趋势判断
  - k4 写 qd_sentiment_event_log 补充背离事件类型 (div_* 前缀)

输出维度 (全实现):
  - 4 大指数涨幅 + 涨跌家数 + 涨跌停家数 + 主力资金
  - 恐慌/贪婪指数 0-100 + 趋势变化
  - 变盘临界警告 (清仓提示) + 恐慌反转信号 (谨慎买入)
"""

import os
import sys
import json
import time
import math
from datetime import datetime

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
from lib.qdb import connect, query_df, executemany_batch, cutoff

# 日志
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k4_sentiment_deep_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

DST_DEEP = 'qd_sentiment_deep'
_DEEP_COLS = [
    'snapshot_time',
    'pg_index', 'pg_signal',
    'bb_ratio', 'bb_signal',
    'rotation_intensity', 'rotation_signal',
    'cycle_phase', 'phase_confidence',
    'capital_sentiment', 'market_main_net', 'capital_consistency', 'dark_money_active',
    'ladder_health', 'ladder_signal',
    'divergence_count', 'divergences',
    'st_ma_zt_cnt_5', 'st_ma_zt_cnt_10',
    'st_ma_fbl_5', 'st_ma_fbl_10',
    'st_ma_udr_5', 'st_ma_udr_10',
    'st_ma_pg_5', 'st_ma_pg_10',
    'calc_duration_ms',
]

# 4 大指数代码映射 (tqcenter 习惯用 000001.SH / 399001.SZ / 399006.SZ / 000688.SH)
_INDEX_LABELS = {
    '000001.SH': '上证',
    '399001.SZ': '深证',
    '399006.SZ': '创业板',
    '000688.SH': '科创50',
}
_INDEX_CODES = list(_INDEX_LABELS.keys())

# ── 恐慌/贪婪指数 阈值 ──
# 每分量 (lo, hi, weight), 加权累加 0-100
_PG_WEIGHTS = [
    ('zt_cnt', 0, 150, 0.20),        # 涨停热度
    ('fbl', 0.0, 90.0, 0.20),        # 封板率 %
    ('udr', 0.0, 4.0, 0.20),         # 涨跌比
    ('max_lb', 0, 10, 0.15),         # 最高连板
    ('main_net', -5e9, 5e9, 0.15),   # 主力净流
    ('pressure_diff', -5e7, 5e7, 0.10),  # 5档压力差
]

# PG 信号标签
_PG_SIGNALS = [
    (0, '恐慌'), (25, '恐惧'), (45, '中性'), (65, '贪婪'), (85, '狂热'),
]


def _sf(v, default=0.0):
    try:
        r = float(v)
        if r != r:
            return default
        return r
    except (TypeError, ValueError):
        return default


def _norm(v, lo, hi):
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _pg_label(pg):
    for threshold, label in _PG_SIGNALS:
        if pg >= threshold:
            continue
        return label
    return '狂热'


def _arrow(v, up='↑', down='↓', flat='→'):
    """方向箭头, 含零值判断"""
    if v > 0.001:
        return up
    if v < -0.001:
        return down
    return flat


def _fmt_yi(v):
    """格式化金额为 亿, v 的单位是元"""
    return f'{v / 1e8:+.2f}亿'


# ══════════════════════════════════════════════════════════════
# 全场读数
# ══════════════════════════════════════════════════════════════

def _load_market_breadth(con):
    """全市场涨跌家数 + 涨跌停家数

    读:
      - qd_pricevol: 涨(Now>LastClose) / 跌 / 平
      - qd_stock_snapshot: FCAmo>0 涨停 / <0 跌停 / 炸板
    返回:
      dict {up_cnt, down_cnt, even_cnt, zt_cnt, dt_cnt, break_cnt, sealed, fbl}
      数据不足返回空 dict
    """
    result = {}

    # 涨跌家数 (pricevol 全场口径)
    try:
        df_pv = query_df(con,
            f"SELECT code, Now, LastClose FROM qd_pricevol "
            f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        if df_pv is not None and not df_pv.empty:
            up = down = even = 0
            for _, r in df_pv.iterrows():
                now = _sf(r.get('Now'))
                lc = _sf(r.get('LastClose'))
                if lc <= 0:
                    continue
                if now > lc:
                    up += 1
                elif now < lc:
                    down += 1
                else:
                    even += 1
            result.update({'up_cnt': up, 'down_cnt': down, 'even_cnt': even})
            udr = (up / down) if down > 0 else (99.0 if up > 0 else 1.0)
            result['udr'] = round(min(udr, 99.0), 2)
    except Exception as e:
        logger.warning('读涨跌家数失败: {}', e)

    # 涨跌停家数 (intraday 表有 FCAmo, snapshot 有 Max)
    # C8 拆表: snapshot_time 不对齐，按 code 取最新再合并，不按精确 timestamp JOIN
    try:
        # 分别取两表最近5min数据
        snap = query_df(con,
            f"SELECT code, Max, snapshot_time FROM qd_stock_snapshot "
            f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        intra = query_df(con,
            f"SELECT code, FCAmo, ZTPrice, snapshot_time FROM qd_stock_intraday "
            f"WHERE snapshot_time > '{cutoff(minutes=5)}'")
        if snap is not None and not snap.empty and intra is not None and not intra.empty:
            # 每个 code 取各表最新
            snap_l = snap.sort_values('snapshot_time').groupby('code', as_index=False).last()
            intra_l = intra.sort_values('snapshot_time').groupby('code', as_index=False).last()
            merged = snap_l.merge(intra_l, on='code', how='left')
            zt = dt = brk = 0
            for _, r in merged.iterrows():
                fcamo = _sf(r.get('FCAmo'))
                mx = _sf(r.get('Max'))
                zt_p = _sf(r.get('ZTPrice'))
                if fcamo > 0:
                    zt += 1
                elif fcamo < 0:
                    dt += 1
                elif zt_p > 0 and mx >= zt_p * 0.999:
                    brk += 1
            sealed = zt + brk
            fbl = round(zt / sealed * 100, 1) if sealed > 0 else 0.0
            result.update({'zt_cnt': zt, 'dt_cnt': dt, 'break_cnt': brk, 'fbl': fbl})
    except Exception as e:
        logger.warning('读涨跌停家数失败: {}', e)

    return result


def _load_index_readings(con):
    """4 大指数涨幅读数

    读 qd_index_snapshot 最近 2 分钟, 取每指数最新一行
    返回:
      dict {code: {zaf, now_val, label}}
      or empty dict
    """
    try:
        df = query_df(con,
            f"SELECT code, Now, LastClose FROM qd_index_snapshot "
            f"WHERE snapshot_time > '{cutoff(minutes=2)}' "
            f"ORDER BY snapshot_time DESC")
        if df is None or df.empty:
            return {}
        latest = df.groupby('code', as_index=False).first()
        idx = {}
        for _, r in latest.iterrows():
            code = r['code']
            now_val = _sf(r.get('Now'))
            lc = _sf(r.get('LastClose'))
            if lc > 0 and now_val > 0:
                zaf = round((now_val - lc) / lc * 100, 2)
                idx[code] = {'zaf': zaf, 'now_val': now_val,
                             'label': _INDEX_LABELS.get(code, code)}
        return idx
    except Exception as e:
        logger.warning('读指数快照失败: {}', e)
        return {}


def _load_capital_flow(con):
    """主力资金流向

    读 qd_sector_flow 最新帧 (5 分钟内):
      - SUM(main_net) 全市场主力净流
      - 正数板块占比 (一致性)

    返回:
      dict {main_net, consistency}
    """
    try:
        df = query_df(con,
            f"SELECT main_net FROM qd_sector_flow "
            f"WHERE flow_time > '{cutoff(minutes=5)}'")
        if df is not None and not df.empty:
            total = df['main_net'].sum()
            pos_ratio = round((df['main_net'] > 0).sum() / max(len(df), 1), 2)
            return {'main_net': total, 'consistency': pos_ratio}
    except Exception as e:
        logger.warning('读资金流失败: {}', e)
    return {'main_net': 0.0, 'consistency': 0.5}


def _load_prev_pg(con):
    """读取上一帧 PG 指数, 用于趋势比较

    返回: prev_pg (float|None)
    """
    try:
        df = query_df(con,
            "SELECT pg_index FROM qd_sentiment_deep "
            "ORDER BY snapshot_time DESC LIMIT 1 OFFSET 1")
        if df is not None and not df.empty and pd.notna(df.iloc[0].get('pg_index')):
            return float(df.iloc[0]['pg_index'])
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════
# PG 指数
# ══════════════════════════════════════════════════════════════

def _calc_pg_index(breadth, cap_flow):
    """6 分量加权 → 恐慌/贪婪指数 0-100

    Args:
      breadth: 来自 _load_market_breadth()
      cap_flow: 来自 _load_capital_flow()

    Returns:
      (pg_index, pg_signal)
      数据不足返回 (None, '')
    """
    raw = {
        'zt_cnt': breadth.get('zt_cnt', 0),
        'fbl': breadth.get('fbl', 0.0),
        'udr': breadth.get('udr', 1.0),
        'max_lb': 0,   # 暂时没有连续帧 max_lb, 后续从 k3 快照读
        'main_net': cap_flow.get('main_net', 0.0),
        'pressure_diff': 0.0,
    }

    # 从 snapshot_min 读 max_lb
    try:
        con_ = connect()
        try:
            df_s = query_df(con_,
                "SELECT max_lb FROM qd_sentiment_snapshot_min "
                "ORDER BY snapshot_time DESC LIMIT 1")
            if df_s is not None and not df_s.empty:
                raw['max_lb'] = int(_sf(df_s.iloc[0].get('max_lb')))
        finally:
            con_.close()
    except Exception:
        pass

    # 检查是否至少 zt_cnt/fbl 有值
    has_signal = raw['zt_cnt'] > 0 or raw['fbl'] > 0
    if not has_signal:
        return None, ''

    total = 0.0
    for key, lo, hi, weight in _PG_WEIGHTS:
        v = _sf(raw.get(key))
        if key in ('main_net', 'pressure_diff'):
            norm = _norm(v, lo, hi)
        else:
            norm = _norm(v, lo, hi)
        total += norm * weight * 100

    pg = round(total, 1)
    sig = _pg_label(pg)
    return pg, sig


# ══════════════════════════════════════════════════════════════
# 背离 & 拐点检测
# ══════════════════════════════════════════════════════════════

def _detect_divergences(index_readings, cap_flow, breadth):
    """背离综合检测 + 变盘临界/恐慌反转信号

    Args:
      index_readings: _load_index_readings() 结果
      cap_flow: _load_capital_flow() 结果
      breadth: _load_market_breadth() 结果

    Returns:
      list[dict]: [{type, desc, priority}]   priority: 'critical' | 'warning' | 'info'
    """
    divs = []

    sh = index_readings.get('000001.SH', {})
    sh_zaf = sh.get('zaf', 0.0)
    main_net = cap_flow.get('main_net', 0.0)
    zt_cnt = breadth.get('zt_cnt', 0)
    fbl = breadth.get('fbl', 0.0)
    udr = breadth.get('udr', 1.0)
    up_cnt = breadth.get('up_cnt', 0)
    down_cnt = breadth.get('down_cnt', 0)

    # 1) 价资背离: 指数涨但资金出
    if sh_zaf > 0.3 and main_net < 0:
        divs.append({
            'type': '价资背离',
            'desc': f"上证涨{sh_zaf:.2f}%但主力净流出{_fmt_yi(main_net)}",
            'priority': 'warning',
        })
    elif sh_zaf < -0.5 and main_net > 0:
        divs.append({
            'type': '价资背离',
            'desc': f"上证跌{sh_zaf:.2f}%但主力净流入{_fmt_yi(main_net)} (资金抄底)",
            'priority': 'info',
        })

    # 2) 价宽背离: 指数分化 (二八)
    if len(index_readings) >= 2:
        zafs = [v['zaf'] for v in index_readings.values()]
        zaf_range = max(zafs) - min(zafs)
        if zaf_range > 1.0:
            parts = [f"{v['label']}{v['zaf']:+.2f}%" for v in index_readings.values()]
            divs.append({
                'type': '指数分化',
                'desc': f"指数强弱分化 {zaf_range:.2f}%: {' '.join(parts)}",
                'priority': 'warning' if sh_zaf > 0 and udr < 0.8 else 'info',
            })

    # 3) 情绪-资金背离: 涨停多但资金流出
    if zt_cnt >= 30 and main_net < 0:
        divs.append({
            'type': '情绪背离',
            'desc': f"涨停{zt_cnt}家但主力净流出{_fmt_yi(main_net)} (诱多风险)",
            'priority': 'critical',
        })

    # 4) 涨跌比极端 + 涨跌停极端
    if udr < 0.3 and zt_cnt < 10:
        divs.append({
            'type': '恐慌潮',
            'desc': f"涨跌比{udr:.2f} 涨{up_cnt}/跌{down_cnt} 涨停仅{zt_cnt}家 (恐慌蔓延)",
            'priority': 'critical',
        })
    elif udr > 5.0 and zt_cnt > 80:
        divs.append({
            'type': '过热',
            'desc': f"涨跌比{udr:.2f} 涨停{zt_cnt}家 (过热信号, 警惕尾盘炸板)",
            'priority': 'warning',
        })

    # 5) 封板率过低
    if 0 < fbl < 50 and zt_cnt >= 20:
        divs.append({
            'type': '炸板潮',
            'desc': f"封板率仅{fbl:.1f}% 涨停{zt_cnt}家 (炸板潮, 追高风险极大)",
            'priority': 'critical',
        })

    return divs


def _detect_turning_point(pg, prev_pg, breadth):
    """变盘临界 / 恐慌反转信号检测

    Args:
      pg: 当前 PG 指数 (None 则跳过)
      prev_pg: 上一帧 PG (None 则跳过)
      breadth: 涨跌读数

    Returns:
      dict | None: {type, desc, action}   action: '清仓' | '谨慎买入' | '警报'
    """
    if pg is None:
        return None

    zt_cnt = breadth.get('zt_cnt', 0)
    fbl = breadth.get('fbl', 0.0)
    udr = breadth.get('udr', 1.0)

    # A) 变盘临界 → 清仓提示
    #    PG>65(贪婪/狂热) 且 封板率<60% → 情绪虚高
    if pg >= 65 and 0 < fbl < 60:
        return {
            'type': '变盘临界',
            'desc': f"PG {pg} 情绪偏热但封板率仅{fbl:.0f}% (赚钱效应差 ↔ 资金谨慎)",
            'action': '⚠ 警惕追高, 可考虑减仓',
        }
    if pg >= 75 and zt_cnt >= 50 and fbl < 75:
        return {
            'type': '变盘临界',
            'desc': f"PG {pg} 情绪过热 {zt_cnt}家涨停, 但封板率{fbl:.0f}% (分歧加大)",
            'action': '⚠ 注意尾盘炸板, 不要追后排',
        }

    # B) 恐慌反转 → 谨慎买入
    #    PG<30(恐慌) 且 涨跌比回升 或 资金回流
    if prev_pg is not None:
        pg_delta = pg - prev_pg
        if pg < 30 and pg_delta > 5:
            return {
                'type': '恐慌反转',
                'desc': f"PG 从 {prev_pg:.0f} 回升至 {pg:.0f} (恐慌缓解 +{pg_delta:+.0f})",
                'action': '⚠ 可小仓位试错, 严格止损',
            }
        if pg_delta < -10 and zt_cnt < 20:
            return {
                'type': '情绪急降',
                'desc': f"PG 从 {prev_pg:.0f} 降至 {pg:.0f} (情绪快速降温 {pg_delta:.0f}点)",
                'action': '⚠ 已持仓注意止盈止损, 新仓观望',
            }
        if pg_delta < -15:
            return {
                'type': '恐慌宣泄',
                'desc': f"PG 骤降 {pg_delta:.0f} 点至 {pg:.0f} (恐慌持续释放)",
                'action': '⚠ 等待企稳再动手, 不要抄跌停板',
            }

    # C) 震荡区提示
    if 40 <= pg <= 60 and zt_cnt >= 40 and fbl >= 75:
        return {
            'type': '健康震荡',
            'desc': f"PG {pg} 中性偏多, {zt_cnt}家涨停封板率{fbl:.0f}% (短线生态良好)",
            'action': '✓ 正常操作, 聚焦核心板块',
        }

    return None


# ══════════════════════════════════════════════════════════════
# 飞书推送 — 直观全景消息
# ══════════════════════════════════════════════════════════════

def push_panoramic(result):
    """推送全市场全景消息 (整条消息浓缩为一眼看完)

    Args:
        result: k4.run() 返回的 dict
    Returns:
        bool
    """
    try:
        import importlib as _il
        _feishu = _il.import_module('feishu')

        idx = result.get('index_readings', {})
        breadth = result.get('breadth', {})
        cap = result.get('capital_flow', {})
        pg = result.get('pg_index')
        pg_sig = result.get('pg_signal', '')
        prev_pg = result.get('prev_pg')
        divs = result.get('divergences', [])
        turn = result.get('turning_point')

        lines = []

        # ── 标题行 ──
        ts = datetime.now().strftime('%H:%M')
        if pg is not None:
            lines.append(f'📊 全景情绪 | PG {pg} {pg_sig} | {ts}')
            if prev_pg is not None:
                delta = pg - prev_pg
                d_arrow = '↑' if delta > 0 else '↓'
                lines.append(f'   (较上期 {d_arrow} {abs(delta):.1f}点, 前值 {prev_pg:.1f})')
        else:
            lines.append(f'📊 全景情绪 | {ts} (数据不足)')
        lines.append('')

        # ── 4 大指数 ──
        if idx:
            parts = []
            for code in _INDEX_CODES:
                v = idx.get(code)
                if v:
                    parts.append(f"{v['label']}{v['zaf']:+.2f}{_arrow(v['zaf'])}")
            if parts:
                lines.append('── 四大指数 ──')
                lines.append('  ' + '  '.join(parts))
                lines.append('')

        # ── 涨跌全景 ──
        uc = breadth.get('up_cnt', '-')
        dc = breadth.get('down_cnt', '-')
        zt = breadth.get('zt_cnt', '-')
        dt_c = breadth.get('dt_cnt', 0)
        brk = breadth.get('break_cnt', 0)
        fbl_v = breadth.get('fbl', 0)

        lines.append(f'── 涨跌全景 ──')
        lines.append(f'  涨 {uc}  跌 {dc}  (涨跌比 {breadth.get("udr", "-"):.2f})')
        lines.append(f'  涨停 {zt}  跌停 {dt_c}  炸板 {brk}  封板率 {fbl_v:.0f}%')
        lines.append('')

        # ── 资金 ──
        mn = cap.get('main_net', 0)
        ccy = cap.get('consistency', 0)
        mn_arrow = _arrow(mn, '🟢', '🔴', '⚪')
        lines.append(f'── 主力资金 ──')
        lines.append(f'  {mn_arrow} {_fmt_yi(mn)}  (一致率 {ccy:.0%})')
        lines.append('')

        # ── 拐点信号（突出）──
        if turn:
            action = turn.get('action', '')
            lines.append(f'── 拐点信号 ──')
            lines.append(f'  {turn["type"]}: {turn["desc"]}')
            lines.append(f'  ▶ {action}')
            lines.append('')

        # ── 背离/异常 ──
        if divs:
            lines.append(f'── 异常信号 ({len(divs)}) ──')
            for d in divs:
                p_mark = {'critical': '🚨', 'warning': '⚡', 'info': '📌'}
                mark = p_mark.get(d.get('priority', 'info'), '📌')
                lines.append(f'  {mark} {d["type"]}: {d["desc"]}')
            lines.append('')

        lines.append('─' * 20)
        lines.append(f'k4 深度情绪 | 5min 自动推送')

        text = '\n'.join(lines)
        ok = _feishu.push_text(text)
        logger.info('全景推送: {}', ok)
        return ok
    except Exception as e:
        logger.warning('k4 全景推送失败: {}', e)
        return False


# ══════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════

def _write_deep(con, now, result, dur_ms):
    """写 qd_sentiment_deep 一行"""
    snap = now.replace(second=0, microsecond=0)
    pg = result.get('pg_index')
    sig = result.get('pg_signal', '')
    cap = result.get('capital_sentiment')
    mn = result.get('market_main_net', 0.0)
    cc = result.get('capital_consistency', 0.0)
    da = result.get('dark_money_active', 0.0)
    divs = result.get('divergences', [])

    row = (
        snap,           # snapshot_time
        pg, sig,        # D1 pg_index, pg_signal
        None, None,       # D2 bb (预留)
        None, None,       # D3 rotation (预留)
        None, None,       # D4 cycle (预留)
        cap, mn, cc, da,  # D5 capital
        None, None,       # D6 ladder (预留)
        len(divs), json.dumps(divs, ensure_ascii=False),  # D7 divergence
        None, None, None, None, None, None, None, None,  # D8 ma (预留)
        dur_ms,         # calc_duration_ms
    )
    try:
        executemany_batch(con, DST_DEEP, _DEEP_COLS, [row])
        return True
    except Exception as e:
        logger.warning('写 qd_sentiment_deep 失败: {}', e)
        return False


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def run(con, ctx=None):
    """深度情绪分析主流程 (5min/轮)

    每轮:
      1. 读全场数据: 4指数 + 涨跌家数 + 涨跌停 + 资金流
      2. 算 PG 恐慌/贪婪指数
      3. 检测背离/拐点信号
      4. 写库 + 推飞书

    Args:
        con: psycopg2 连接
        ctx: StrategyContext (可选, 挂 ctx.sentiment_deep)

    Returns:
        dict: 含所有读数, 供推送和 ctx 消费
    """
    t0 = time.time()
    now = datetime.now()
    logger.info('▶ k4 深度情绪计算开始')

    # 1. 全场原始数据
    index_readings = _load_index_readings(con)
    breadth = _load_market_breadth(con)
    cap_flow = _load_capital_flow(con)

    # 2. PG 指数
    pg, sig = _calc_pg_index(breadth, cap_flow)
    prev_pg = _load_prev_pg(con) if pg is not None else None

    # 3. 背离 + 拐点
    divs = _detect_divergences(index_readings, cap_flow, breadth)
    turn = _detect_turning_point(pg, prev_pg, breadth)

    # 4. 合成结果
    result = {
        'pg_index': pg,
        'pg_signal': sig or '',
        'prev_pg': prev_pg,
        # 原始数据 (供推送和 ctx)
        'index_readings': index_readings,
        'breadth': breadth,
        'capital_flow': cap_flow,
        # 资金情绪 (D5 兼容)
        'capital_sentiment': (pg - 50) * 2 if pg is not None else 0,  # 粗略映射
        'market_main_net': cap_flow.get('main_net', 0.0),
        'capital_consistency': cap_flow.get('consistency', 0.5),
        'dark_money_active': 0.0,
        # 背离
        'divergences': divs,
        'divergence_count': len(divs),
        'turning_point': turn,
    }

    dur_ms = int((time.time() - t0) * 1000)
    _write_deep(con, now, result, dur_ms)

    # 推送 (有拐点信号或背离就推, 否则每轮都推 — 用户要的是「一眼看到」)
    # 拐点 / 背离 必推; 普通轮次静默写库
    should_push = turn is not None or len(divs) > 0

    if should_push:
        push_panoramic(result)

    # 飞书多维表格写入 (每 5min 一行, 不管有没有信号)
    try:
        import importlib as _il
        _bw = _il.import_module('feishu.bitable_writer')
        bitable_token = getattr(_bw._cfg, 'BITABLE_TOKEN', '')
        if bitable_token:
            _bw.write_panorama_row(bitable_token, result)
        else:
            logger.debug('BITABLE_TOKEN 未配置, 跳过全景情绪写表')
    except Exception as e:
        logger.warning('全景情绪写多维表格失败: {}', e)

    logger.info('✓ k4 完成 ({}ms): PG={} {} 拐点={} 背离={} 推送={}',
                dur_ms, pg, sig,
                turn['type'] if turn else '无',
                len(divs), should_push)

    if ctx is not None:
        ctx.sentiment_deep = result

    return result


if __name__ == '__main__':
    logger.info('=== k4 独立运行测试 ===')
    con = connect()
    try:
        r = run(con)
        print('\nk4 结果摘要:')
        if r.get('pg_index') is not None:
            print(f'  PG: {r["pg_index"]} {r["pg_signal"]}')
        else:
            print('  PG: (数据不足)')
        idx = r.get('index_readings', {})
        for code in _INDEX_CODES:
            v = idx.get(code)
            if v:
                print(f'  {v["label"]}: {v["zaf"]:+.2f}%')
        b = r.get('breadth', {})
        print(f'  涨跌: {b.get("up_cnt","-")}/{b.get("down_cnt","-")}  涨停{ b.get("zt_cnt","-")}')
        cf = r.get('capital_flow', {})
        print(f'  主力: {_fmt_yi(cf.get("main_net",0))}')
        turn = r.get('turning_point')
        if turn:
            print(f'  拐点: {turn["type"]} → {turn["action"]}')
        for d in r.get('divergences', []):
            print(f'  信号: [{d["priority"]}] {d["type"]}: {d["desc"]}')
        print(f'\n  全景推送: 已触发' if (turn or r.get('divergences'))
              else f'\n  全景推送: 当前无异常, 静默写库')
    finally:
        con.close()
