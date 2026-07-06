"""k4: 板块热力图 + 最强个股梯队

脚本路径: K:\QuestDB_test\\compute\\k4_sector_heatmap.py
用途: 4 组板块排行 (行业一级/行业二级/行业三级/概念各 Top 5) +
      每组最强板块的个股梯队 Top 3
数据源: qd_sector_snapshot + qd_stock_snapshot + lib.relation_graph
写入表: qd_sector_heatmap (5min/轮)
推送: 飞书推送 (普通轮次静默写库, 排行榜变更时推送)
频率: 5min/轮 (由 intraday_loop 60s 块的条件计数器触发, 同 k4)

板块筛选逻辑:
  - relation_graph.get_sector_raw_type(code) 返回 '行业一级'/'行业二级'/'行业三级'/'概念板块'
  - 读 qd_sector_snapshot 最新帧, 按 code 匹配, 按 ZAF (涨幅) 排序
  - 涨停家数: 读板块成分股的 FCAmo > 0 计数 (qd_stock_snapshot focus 池口径)

个股梯队逻辑:
  - 每组 Top1 板块, relation_graph.get_sector_stocks(block_code) 取成分股
  - 从 qd_stock_snapshot 读取最新 ZAF + FCAmo
  - 按 ZAF 排序取 Top 3, 标记涨停状态
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
from lib.relation_graph import get_sector_raw_type, get_sector_stocks, get_stock_sectors, _sector_meta

# 日志
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k4_sector_heatmap_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

DST_HEATMAP = 'qd_sector_heatmap'
_HEATMAP_COLS = [
    'snapshot_time',
    'industry_l1_ranking', 'industry_l2_ranking', 'industry_l3_ranking', 'concept_ranking',
    'industry_l1_stocks', 'industry_l2_stocks', 'industry_l3_stocks', 'concept_stocks',
    'calc_duration_ms',
]

# 4 组配置: (结果键, 原始分类类型, 显示名)
_GROUP_CONFIG = [
    ('industry_l1', '行业一级', '行业一级'),
    ('industry_l2', '行业二级', '行业二级'),
    ('industry_l3', '行业三级', '行业三级'),
    ('concept', '概念板块', '概念板块'),
]

_TOP_N_SECTORS = 5
_TOP_N_STOCKS = 3


def _sf(v, default=0.0):
    try:
        r = float(v)
        if r != r:
            return default
        return r
    except (TypeError, ValueError):
        return default


def _is_zt(fcamo):
    """涨停判定: FCAmo > 0"""
    return _sf(fcamo) > 0


def _zm(v, up='↑', down='↓', flat='→'):
    if v > 0.001:
        return up
    if v < -0.001:
        return down
    return flat


def _get_sector_name(code):
    """取板块名"""
    m = _sector_meta.get(code)
    return m.get('sector_name', code) if m else code


# ══════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════

def _load_sector_zaf(con):
    """读板块快照: qd_sector_snapshot (c2 60s轮换写入, 数据在 snapshot 表)

    C8 拆表后: 板块快照没有对应的 intraday 表, 涨幅直接由 Now/LastClose 算。

    返回:
      dict[block_code, {'zaf': float, 'now': float}]
    """
    try:
        df = query_df(con,
            f"SELECT code, Now, LastClose FROM qd_sector_snapshot "
            f"WHERE snapshot_time > '{cutoff(minutes=5)}' "  # 放宽到5分钟, c2 60s全市场轮换
            f"ORDER BY snapshot_time DESC")
        if df is None or df.empty:
            return {}
        latest = df.groupby('code', as_index=False).first()
        result = {}
        for _, r in latest.iterrows():
            code = r['code']
            now = _sf(r.get('Now'))
            lc = _sf(r.get('LastClose'))
            if lc > 0:
                zaf = round((now - lc) / lc * 100, 2)
                result[code] = {'zaf': zaf, 'now': now}
        return result
    except Exception as e:
        logger.warning('读板块快照失败: {}', e)
        return {}


def _load_stock_data(con):
    """读个股快照: 从 qd_stock_intraday 取 FCAmo, 从 qd_stock_snapshot 取 Now/LastClose

    C8 拆表后: FCAmo 在 qd_stock_intraday 表, JOIN snapshot 取完整数据。

    返回:
      dict[stock_code, {'zaf': float, 'fcamo': float}]
    """
    try:
        df = query_df(con,
            f"SELECT s.code, s.Now, s.LastClose, i.FCAmo "
            f"FROM qd_stock_intraday i "
            f"JOIN qd_stock_snapshot s ON i.code = s.code AND i.snapshot_time = s.snapshot_time "
            f"WHERE i.snapshot_time > '{cutoff(minutes=5)}'")
        if df is None or df.empty:
            return {}
        latest = df.groupby('code', as_index=False).first()
        result = {}
        for _, r in latest.iterrows():
            code = r['code']
            now = _sf(r.get('Now'))
            lc = _sf(r.get('LastClose'))
            fcamo = _sf(r.get('FCAmo'))
            if lc > 0:
                zaf = round((now - lc) / lc * 100, 2)
                result[code] = {'zaf': zaf, 'fcamo': fcamo}
        return result
    except Exception as e:
        logger.warning('读个股快照失败: {}', e)
        return {}


# ══════════════════════════════════════════════════════════════
# 计算
# ══════════════════════════════════════════════════════════════

def _get_sector_codes_by_raw_type(raw_type):
    """从 _sector_raw_type 取所有匹配原始分类类型的板块代码列表

    Args:
        raw_type: '行业一级' | '行业二级' | '行业三级' | '概念板块'
    Returns:
        list[str]: 板块代码列表
    """
    from lib.relation_graph import _sector_raw_type as srt
    return [code for code, rt in srt.items() if rt == raw_type]


def _compute_zt_count(block_code, stock_data):
    """统计板块成分股中涨停家数 (FCAmo > 0)

    从 relation_graph 取成分股, 匹配 stock_data 中的 FCAmo
    """
    stocks = get_sector_stocks(block_code)
    if not stocks or not stock_data:
        return 0
    zt = 0
    for s in stocks:
        sc = s.get('code', '')
        sd = stock_data.get(sc)
        if sd and _is_zt(sd.get('fcamo')):
            zt += 1
    return zt


def _compute_sector_ranking(raw_type, sector_zaf, stock_data):
    """计算一组板块排行 Top N

    Args:
        raw_type: 原始分类类型
        sector_zaf: _load_sector_zaf() 结果
        stock_data: _load_stock_data() 结果

    Returns:
        list[dict]: [{code, name, zaf, zt_count}, ...] 按 ZAF 降序
    """
    codes = _get_sector_codes_by_raw_type(raw_type)
    if not codes:
        logger.warning('无 {} 板块 (raw_type={})', '板块', raw_type)
        return []

    scored = []
    for code in codes:
        sz = sector_zaf.get(code)
        if sz is None:
            # 板块代码不在 sector_snapshot 中 (881xxx 行业板块), 回退成分股计算
            stocks = get_sector_stocks(code)
            if not stocks:
                continue
            zaf_sum = 0.0
            zaf_cnt = 0
            for s in stocks:
                sc = s.get('code', '')
                sd = stock_data.get(sc)
                if sd and sd.get('zaf', 0.0) != 0.0:
                    zaf_sum += sd['zaf']
                    zaf_cnt += 1
            if zaf_cnt == 0:
                continue
            zaf = round(zaf_sum / zaf_cnt, 2)
            sz = {'zaf': zaf, 'now': 0.0}
        zt_cnt = _compute_zt_count(code, stock_data)
        scored.append({
            'code': code,
            'name': _get_sector_name(code),
            'zaf': sz['zaf'],
            'zt_count': zt_cnt,
        })

    scored.sort(key=lambda x: -x['zaf'])
    return scored[:_TOP_N_SECTORS]


def _compute_stock_tier(block_code, stock_data):
    """计算某板块最强个股梯队 Top N

    Args:
        block_code: 板块代码
        stock_data: _load_stock_data() 结果

    Returns:
        list[dict]: [{code, name, zaf, is_zt}, ...] 按 ZAF 降序
    """
    stocks = get_sector_stocks(block_code)
    if not stocks or not stock_data:
        return []

    scored = []
    for s in stocks:
        sc = s.get('code', '')
        sd = stock_data.get(sc)
        if sd is None:
            continue
        scored.append({
            'code': sc,
            'name': s.get('name', sc),
            'zaf': sd['zaf'],
            'is_zt': _is_zt(sd.get('fcamo')),
        })

    scored.sort(key=lambda x: -x['zaf'])
    return scored[:_TOP_N_STOCKS]


# ══════════════════════════════════════════════════════════════
# 推送
# ══════════════════════════════════════════════════════════════

def push_heatmap(result):
    """推送板块热力图 + 个股梯队到飞书

    Args:
        result: run() 返回的 dict
    Returns:
        bool
    """
    try:
        import importlib as _il
        _feishu = _il.import_module('feishu')

        lines = []
        ts = datetime.now().strftime('%H:%M')
        lines.append(f'──── 最强板块 · 热力图 {ts} ────')
        lines.append('')

        # 4 组板块排行
        for key, _raw_type, label in _GROUP_CONFIG:
            ranking = result.get(f'{key}_ranking', [])
            if not ranking:
                continue
            lines.append(f'── {label} Top {_TOP_N_SECTORS} ──')
            for i, s in enumerate(ranking, 1):
                zt_str = f' 涨停{s["zt_count"]}' if s['zt_count'] > 0 else ''
                arrow = _zm(s['zaf'])
                lines.append(f'  {i}. {s["name"]} {s["zaf"]:+.2f}{arrow}{zt_str}')
            lines.append('')

        # 个股梯队
        lines.append(f'──── 最强梯队 · 个股 ────')
        lines.append('')
        for key, _raw_type, label in _GROUP_CONFIG:
            stocks_key = f'{key}_stocks'
            ranking_key = f'{key}_ranking'
            ranking = result.get(ranking_key, [])
            stocks = result.get(stocks_key, [])
            if not ranking or not stocks:
                continue
            top_sector = ranking[0]
            lines.append(f'{label}·{top_sector["name"]} [最强]:')
            for s in stocks:
                zt_mark = '📈涨停' if s.get('is_zt') else ''
                lines.append(f'  {s["name"]}({s["code"]}) {s["zaf"]:+.2f}% {zt_mark}'.strip())
            lines.append('')

        lines.append('─' * 22)
        lines.append('k4 板块梯队 | 5min 自动推送')

        text = '\n'.join(lines)
        ok = _feishu.push_text(text)
        logger.info('板块梯队推送: {}', ok)
        return ok
    except Exception as e:
        logger.warning('k4 板块梯队推送失败: {}', e)
        return False


# ══════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════

def _write_heatmap(con, now, result, dur_ms):
    """写 qd_sector_heatmap 一行"""
    snap = now.replace(second=0, microsecond=0)

    def _to_json(lst):
        return json.dumps(lst, ensure_ascii=False)

    row = (
        snap,
        _to_json(result.get('industry_l1_ranking', [])),
        _to_json(result.get('industry_l2_ranking', [])),
        _to_json(result.get('industry_l3_ranking', [])),
        _to_json(result.get('concept_ranking', [])),
        _to_json(result.get('industry_l1_stocks', [])),
        _to_json(result.get('industry_l2_stocks', [])),
        _to_json(result.get('industry_l3_stocks', [])),
        _to_json(result.get('concept_stocks', [])),
        dur_ms,
    )
    try:
        executemany_batch(con, DST_HEATMAP, _HEATMAP_COLS, [row])
        return True
    except Exception as e:
        logger.warning('写 qd_sector_heatmap 失败: {}', e)
        return False


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def run(con, ctx=None):
    """板块热力图 + 个股梯队主流程 (5min/轮)

    Args:
        con: psycopg2 连接
        ctx: StrategyContext (可选, 挂 ctx.sector_heatmap)

    Returns:
        dict: 含所有排行和梯队数据
    """
    t0 = time.time()
    now = datetime.now()
    logger.info('▶ k4 板块热力图计算开始')

    # 1. 加载行情数据
    sector_zaf = _load_sector_zaf(con)
    stock_data = _load_stock_data(con)

    if not sector_zaf:
        logger.warning('板块热力图: 无板块行情数据, 跳过')
        return {}

    # 2. 计算 4 组板块排行 + 个股梯队
    result = {}
    for key, raw_type, _label in _GROUP_CONFIG:
        ranking = _compute_sector_ranking(raw_type, sector_zaf, stock_data)
        result[f'{key}_ranking'] = ranking

        # 个股梯队: 取 Top1 板块
        if ranking:
            top_code = ranking[0]['code']
            stocks = _compute_stock_tier(top_code, stock_data)
            result[f'{key}_stocks'] = stocks
        else:
            result[f'{key}_stocks'] = []

    # 3. 写库
    dur_ms = int((time.time() - t0) * 1000)
    _write_heatmap(con, now, result, dur_ms)

    # 4. 推送 (有数据就推)
    has_data = any(result.get(f'{key}_ranking') for key, _, _ in _GROUP_CONFIG)
    if has_data:
        push_heatmap(result)
    else:
        logger.info('板块热力图: 无有效排行数据, 不推送')

    logger.info('✓ k4 板块热力图完成 ({}ms): 行业L1={} L2={} L3={} 概念={}',
                dur_ms,
                len(result.get('industry_l1_ranking', [])),
                len(result.get('industry_l2_ranking', [])),
                len(result.get('industry_l3_ranking', [])),
                len(result.get('concept_ranking', [])))

    if ctx is not None:
        ctx.sector_heatmap = result

    return result


if __name__ == '__main__':
    logger.info('=== k4 板块热力图独立测试 ===')
    con = connect()
    try:
        r = run(con)
        print('\n板块排行摘要:')
        for key, _, label in _GROUP_CONFIG:
            rank = r.get(f'{key}_ranking', [])
            stocks_ = r.get(f'{key}_stocks', [])
            print(f'\n  {label} Top5:')
            if rank:
                for i, s in enumerate(rank, 1):
                    print(f'    {i}. {s["name"]} {s["zaf"]:+.2f}% 涨停{s["zt_count"]}')
                print(f'    最强个股:')
                for s in stocks_:
                    zt = ' 📈涨停' if s.get('is_zt') else ''
                    print(f'      {s["name"]}({s["code"]}) {s["zaf"]:+.2f}%{zt}')
            else:
                print(f'    (无数据)')
    finally:
        con.close()
