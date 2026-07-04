"""明暗资金计算

脚本路径: K:\QuestDB_test\\strategy\\dark_money.py
用途: 单只/批量计算个股明暗资金, 识别主力真实意图
依赖: pandas, loguru
数据源:
  - snapshot (qd_stock_snapshot) 含 5 档 Buyp1-5/Buyv1-5/Sellp1-5/Sellv1-5/Amount/NowVol/TickDiff
  - more_info (STOCK_INTRADAY_FIELDS) 含 Zjl/Zjl_HB/FCAmo/FCb/Wtb
字段说明 (输入):
  - 明资金 (显性主力): Zjl 主力净额 / FCAmo 大单成交额 (Zjl_HB/FCb 仅中间量, 不入库)
  - 暗资金 (隐性主力): cancel_diff 撤单差分 / order_imbalance 订单不平衡 / wtb 委托买卖比
计算公式 (综合资金流 net_flow):
  net_flow = zjl + cancel_diff*0.3 + order_imbalance*0.001 + wtb*fcamo*0.01
输出列 (对齐 qd_money_flow DDL):
  main_net=zjl / dark_money=cancel_diff / buy_pressure,sell_pressure=Σp*v 5档加权
  pressure_diff_5level=buy-sell / net_flow=综合资金流; big_order_diff,light_money 暂 None
说明:
  - calc 单只计算: cancel_diff 单帧置 0 (calc_batch 跨帧 NowVol.diff 填入)
  - calc_batch 批量计算: 按 code + snapshot_time 排序, 相邻帧差分得 cancel_diff
  - C8 拆表后: 调用方 (_run_money_flow) 传的 df 已含 merge 进来的 intraday 字段
    (Zjl/FCAmo/Wtb 等, 来自 qd_stock_intraday), df_more_info=None; cancel_diff 用 NowVol 跨帧差分
  - order_imbalance = Σ(Buyv1-5) - Σ(Sellv1-5); _safe_float 过滤 NaN (tqcenter 偶返 nan)
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
        r = float(v)
    except (TypeError, ValueError):
        return default
    if r != r:  # NaN (tqcenter 偶返 nan, 需显式过滤, 否则污染 main_net/net_flow)
        return default
    return r


def _sum_levels(data, prefix):
    """累加 5 档值 (Buyp/Buyv/Sellp/Sellv)"""
    total = 0.0
    for i in _LEVELS:
        total += _safe_float(data.get(f'{prefix}{i}'))
    return total


def _weighted_pressure(data):
    """委买/委卖加权压力 = (ΣBuyp_i*Buyv_i, ΣSellp_i*Sellv_i)

    返回 (buy_pressure, sell_pressure); 委托买卖比 wtb = buy/sell, 压力差 = buy-sell。
    """
    buy_pressure = 0.0
    sell_pressure = 0.0
    for i in _LEVELS:
        bp = _safe_float(data.get(f'Buyp{i}'))
        bv = _safe_float(data.get(f'Buyv{i}'))
        sp = _safe_float(data.get(f'Sellp{i}'))
        sv = _safe_float(data.get(f'Sellv{i}'))
        buy_pressure += bp * bv
        sell_pressure += sp * sv
    return buy_pressure, sell_pressure


def calc(snapshot, more_info) -> dict:
    """单只股票明暗资金计算 (输出列对齐 qd_money_flow DDL)

    Args:
        snapshot: qd_stock_snapshot 行 dict (含 5 档 Buyp/Buyv/Sellp/Sellv)
        more_info: 同一行 dict (含 Zjl/FCAmo/Wtb 等 intraday 字段;
                   calc_batch 已前向填充, 故与 snapshot 同源即可)

    Returns:
        dict: {main_net, dark_money, buy_pressure, sell_pressure,
               pressure_diff_5level, net_flow}
        - main_net  = Zjl 主力净额
        - dark_money = cancel_diff 撤单差分 (单帧置 0, calc_batch 跨帧差分)
        - buy_pressure / sell_pressure = 5 档委买/委卖加权 (Σ Buyp_i*Buyv_i / Σ Sellp_i*Sellv_i)
        - pressure_diff_5level = buy_pressure - sell_pressure
        - net_flow = zjl + cancel_diff*0.3 + order_imbalance*0.001 + wtb*fcamo*0.01
    """
    snapshot = snapshot or {}
    more_info = more_info or {}

    # 明资金
    zjl = _safe_float(more_info.get('Zjl'))
    fcamo = _safe_float(more_info.get('FCAmo'))

    # 暗资金
    cancel_diff = _safe_float(snapshot.get('cancel_diff'))  # calc_batch 跨帧差分填入; 单帧调用无此键→0
    order_imbalance = _sum_levels(snapshot, 'Buyv') - _sum_levels(snapshot, 'Sellv')
    wtb = _safe_float(more_info.get('Wtb'))
    buy_pressure, sell_pressure = _weighted_pressure(snapshot)

    # 综合资金流
    net_flow = (zjl
                + cancel_diff * _W_CANCEL
                + order_imbalance * _W_IMBALANCE
                + wtb * fcamo * _W_WTB_FCAMO)

    return {
        'main_net': round(zjl, 2),
        'dark_money': round(cancel_diff, 2),
        'buy_pressure': round(buy_pressure, 2),
        'sell_pressure': round(sell_pressure, 2),
        'pressure_diff_5level': round(buy_pressure - sell_pressure, 2),
        'net_flow': round(net_flow, 2),
    }


def calc_batch(df_snapshot, df_more_info=None) -> pd.DataFrame:
    """批量计算明暗资金 (输出对齐 qd_money_flow DDL)

    Args:
        df_snapshot: qd_stock_snapshot DataFrame, 含 code/snapshot_time + 5 档 + NowVol;
                     若已含 Zjl/FCAmo/Wtb 等 intraday 字段 (调用方前向填充), df_more_info 传 None。
        df_more_info: 可选, 独立 intraday DataFrame (含 code/snapshot_time + Zjl/FCAmo/Wtb)。
                      注意: 若与 df_snapshot 列名重叠, merge 会加后缀致取值失败 ——
                      此时请由调用方前向填充后传 None (intraday_loop._run_money_flow 即如此)。

    Returns:
        DataFrame: 列 [code, flow_time, main_net, big_order_diff, dark_money, light_money,
                   pressure_diff_5level, buy_pressure, sell_pressure, net_flow]
                   big_order_diff / light_money 暂无干净来源, 填 None。
    """
    if df_snapshot is None or df_snapshot.empty:
        logger.warning('明暗资金: snapshot 为空')
        return pd.DataFrame()

    # 合并 snapshot + more_info (按 code + snapshot_time 左连接); 调用方已前向填充时传 None 跳过
    df = df_snapshot.copy()
    if df_more_info is not None and not df_more_info.empty:
        merge_keys = ['code', 'snapshot_time']
        if all(k in df_more_info.columns for k in merge_keys):
            df = df.merge(df_more_info[merge_keys + ['Zjl', 'Zjl_HB', 'FCAmo',
                                                     'FCb', 'Wtb']],
                          on=merge_keys, how='left', suffixes=('', '_mi'))
        else:
            logger.warning('more_info 缺合并键 {}, 仅用 snapshot 估算', merge_keys)
            for col in ('Zjl', 'Zjl_HB', 'FCAmo', 'FCb', 'Wtb'):
                if col not in df.columns:
                    df[col] = 0.0

    # 按 code + snapshot_time 排序, 相邻帧差分得 cancel_diff (用 NowVol 代理)
    sort_cols = [c for c in ('code', 'snapshot_time') if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    if 'NowVol' in df.columns:
        df['cancel_diff'] = df.groupby('code')['NowVol'].diff().fillna(0.0)
    else:
        df['cancel_diff'] = 0.0

    # 逐行计算 (calc 从行 dict 读 cancel_diff → dark_money)
    results = []
    for _, r in df.iterrows():
        results.append(calc(r.to_dict(), r.to_dict()))

    out = pd.DataFrame(results)
    for col in ('code', 'snapshot_time'):
        if col in df.columns:
            out[col] = df[col].values
    # DDL 对齐: snapshot_time→flow_time, 补无来源列, 按 qd_money_flow 列顺序输出
    if 'snapshot_time' in out.columns:
        out = out.rename(columns={'snapshot_time': 'flow_time'})
    out['big_order_diff'] = None
    out['light_money'] = None
    cols = ['code', 'flow_time', 'main_net', 'big_order_diff', 'dark_money',
            'light_money', 'pressure_diff_5level', 'buy_pressure',
            'sell_pressure', 'net_flow']
    out = out[[c for c in cols if c in out.columns]]

    inflow = int((out['net_flow'] > 0).sum()) if not out.empty and 'net_flow' in out.columns else 0
    outflow = int((out['net_flow'] < 0).sum()) if not out.empty and 'net_flow' in out.columns else 0
    logger.info('明暗资金批量计算: {} 行, 流入={} 流出={}', len(out), inflow, outflow)
    return out
