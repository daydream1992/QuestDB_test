"""明暗资金计算

脚本路径: K:\QuestDB_test\\strategy\\dark_money.py
用途: 单只/批量计算个股明暗资金, 识别主力真实意图
依赖: pandas, loguru
数据源:
  - snapshot (qd_stock_snapshot) 含 5 档 Buyp1-5/Buyv1-5/Sellp1-5/Sellv1-5/Amount/NowVol/TickDiff
  - more_info (STOCK_INTRADAY_FIELDS) 含 Zjl/Zjl_HB/FCAmo/FCb/Wtb
字段说明:
  - 明资金 (显性主力): Zjl 主力净额 / Zjl_HB 主力净额环比 / FCAmo 大单成交额 / FCb 大单净额
  - 暗资金 (隐性主力): cancel_diff 撤单差分 / order_imbalance 订单不平衡 / wtb 委托买卖比
                       / pressure_diff 委买委卖加权压力差
计算公式 (综合资金流):
  total_flow = zjl + cancel_diff*0.3 + order_imbalance*0.001 + wtb*fcamo*0.01
说明:
  - calc 单只计算: snapshot/more_info 为单帧 dict, cancel_diff 单帧置 0 (需前后帧)
  - calc_batch 批量计算: 按 code + snapshot_time 排序, 相邻帧差分得 cancel_diff
  - pressure_diff = Σ(Buyp_i*Buyv_i) - Σ(Sellp_i*Sellv_i) (委买加权 - 委卖加权)
  - order_imbalance = Σ(Buyv1-5) - Σ(Sellv1-5)
"""

import pandas as pd
from loguru import logger

# 5 档索引
_LEVELS = (1, 2, 3, 4, 5)

# 综合公式权重 (来自任务规范)
_W_CANCEL = 0.3
_W_IMBALANCE = 0.001
_W_WTB_FCAMO = 0.01


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _sum_levels(data, prefix):
    """累加 5 档值 (Buyp/Buyv/Sellp/Sellv)"""
    total = 0.0
    for i in _LEVELS:
        total += _safe_float(data.get(f'{prefix}{i}'))
    return total


def _weighted_pressure(data):
    """委买委卖加权压力差 = Σ(Buyp_i*Buyv_i) - Σ(Sellp_i*Sellv_i)"""
    buy_pressure = 0.0
    sell_pressure = 0.0
    for i in _LEVELS:
        bp = _safe_float(data.get(f'Buyp{i}'))
        bv = _safe_float(data.get(f'Buyv{i}'))
        sp = _safe_float(data.get(f'Sellp{i}'))
        sv = _safe_float(data.get(f'Sellv{i}'))
        buy_pressure += bp * bv
        sell_pressure += sp * sv
    return buy_pressure - sell_pressure


def calc(snapshot, more_info) -> dict:
    """单只股票明暗资金计算

    Args:
        snapshot: qd_stock_snapshot 行 dict (含 5 档 + Amount/NowVol)
        more_info: STOCK_INTRADAY_FIELDS dict (含 Zjl/FCAmo/Wtb)

    Returns:
        dict: {zjl, zjl_hb, fcamo, fcb, cancel_diff, order_imbalance,
               wtb, pressure_diff, total_flow, label}
        label: 'inflow' / 'outflow' / 'neutral'
    """
    snapshot = snapshot or {}
    more_info = more_info or {}

    # 明资金
    zjl = _safe_float(more_info.get('Zjl'))
    zjl_hb = _safe_float(more_info.get('Zjl_HB'))
    fcamo = _safe_float(more_info.get('FCAmo'))
    fcb = _safe_float(more_info.get('FCb'))

    # 暗资金
    cancel_diff = 0.0  # 单帧无法差分, calc_batch 中按帧差分
    order_imbalance = _sum_levels(snapshot, 'Buyv') - _sum_levels(snapshot, 'Sellv')
    wtb = _safe_float(more_info.get('Wtb'))
    pressure_diff = _weighted_pressure(snapshot)

    # 综合资金流
    total_flow = (zjl
                  + cancel_diff * _W_CANCEL
                  + order_imbalance * _W_IMBALANCE
                  + wtb * fcamo * _W_WTB_FCAMO)

    if total_flow > 0:
        label = 'inflow'
    elif total_flow < 0:
        label = 'outflow'
    else:
        label = 'neutral'

    return {
        'zjl': round(zjl, 2),
        'zjl_hb': round(zjl_hb, 2),
        'fcamo': round(fcamo, 2),
        'fcb': round(fcb, 2),
        'cancel_diff': round(cancel_diff, 2),
        'order_imbalance': round(order_imbalance, 2),
        'wtb': round(wtb, 4),
        'pressure_diff': round(pressure_diff, 2),
        'total_flow': round(total_flow, 2),
        'label': label,
    }


def calc_batch(df_snapshot, df_more_info) -> pd.DataFrame:
    """批量计算明暗资金

    Args:
        df_snapshot: qd_stock_snapshot DataFrame, 含 code/snapshot_time + 5 档 + Amount/NowVol
        df_more_info: more_info DataFrame, 含 code/snapshot_time + Zjl/FCAmo/Wtb 等

    Returns:
        DataFrame: 列 [code, snapshot_time, zjl, zjl_hb, fcamo, fcb,
                  cancel_diff, order_imbalance, wtb, pressure_diff, total_flow, label]
    """
    if df_snapshot is None or df_snapshot.empty:
        logger.warning('明暗资金: snapshot 为空')
        return pd.DataFrame()

    # 合并 snapshot + more_info (按 code + snapshot_time 左连接)
    df = df_snapshot.copy()
    if df_more_info is not None and not df_more_info.empty:
        merge_keys = ['code', 'snapshot_time']
        if all(k in df_more_info.columns for k in merge_keys):
            df = df.merge(df_more_info[merge_keys + ['Zjl', 'Zjl_HB', 'FCAmo',
                                                     'FCb', 'Wtb']],
                          on=merge_keys, how='left')
        else:
            logger.warning('more_info 缺合并键 {}, 仅用 snapshot 估算', merge_keys)
            for col in ('Zjl', 'Zjl_HB', 'FCAmo', 'FCb', 'Wtb'):
                df[col] = 0.0

    # 按 code + snapshot_time 排序, 相邻帧差分得 cancel_diff (用 NowVol 代理)
    sort_cols = [c for c in ('code', 'snapshot_time') if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    if 'NowVol' in df.columns:
        df['cancel_diff'] = df.groupby('code')['NowVol'].diff().fillna(0.0)
    else:
        df['cancel_diff'] = 0.0

    # 逐行计算
    results = []
    for _, r in df.iterrows():
        results.append(calc(r.to_dict(), r.to_dict()))

    out = pd.DataFrame(results)
    for col in ('code', 'snapshot_time'):
        if col in df.columns:
            out[col] = df[col].values
    logger.info('明暗资金批量计算: {} 行, 流入={} 流出={}',
                len(out),
                (out['label'] == 'inflow').sum() if not out.empty else 0,
                (out['label'] == 'outflow').sum() if not out.empty else 0)
    return out
