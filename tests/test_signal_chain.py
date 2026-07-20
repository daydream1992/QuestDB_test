"""端到端信号链测试 (用 2026-07-09 历史数据)

验证: k6板块联动 → k7票型分类 → p28信号检测
重点: focus 池用强势板块成分股 (避免漏掉小盘急涨股)
"""

import sys
import os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from lib.qdb import connect, query_df
from lib.relation_graph import load_from_json, get_sector_stocks, get_stock_name
from compute.k6_linkage import calc_linkage_score, _get_size_tier, _load_config as k6_cfg
from compute.k7_stock_type import classify_stock_type, calc_concept_heat, _load_config as k7_cfg, _load_daily_features, _load_lhb_features
from strategy.plugins.p28_sector_focus_signal import SectorFocusSignalStrategy


class CtxStub:
    pass


def main():
    load_from_json()
    con = connect()
    try:
        st, et = '2026-07-09T14:47:10', '2026-07-09T14:57:10'

        # 1. 板块数据
        flow_df = query_df(con, f"SELECT code, main_net FROM qd_sector_flow WHERE flow_time >= '{st}' AND flow_time <= '{et}' ORDER BY main_net DESC LIMIT 50")
        snap_sec = query_df(con, f"SELECT code, Outside FROM qd_sector_snapshot WHERE snapshot_time >= '{st}' AND snapshot_time <= '{et}'")
        sector_zt = {r['code']: r['Outside'] for _, r in snap_sec.iterrows()} if not snap_sec.empty else {}
        ladder_df = query_df(con, "SELECT code, EverZTCount FROM qd_stock_daily WHERE date = '2026-07-08' AND EverZTCount > 0")
        ladder = {r['code']: r['EverZTCount'] for _, r in ladder_df.iterrows()} if not ladder_df.empty else {}

        cfg6 = k6_cfg()
        strong_codes = set()
        block_info = {}
        for _, fr in flow_df.iterrows():
            bc = fr['code']
            comps = get_sector_stocks(bc)
            if not comps:
                continue
            cc = {c['code'] for c in comps}
            strong_codes |= cc
            _, zt_full = _get_size_tier(len(comps), cfg6)
            block_info[bc] = {'main_net': fr['main_net'], 'zt': sector_zt.get(bc, 0), 'comp': cc, 'zt_full': zt_full}
        print(f"强势板块成分股: {len(strong_codes)} 只")

        # 2. focus_df = 强势成分股 ∩ snapshot+intraday (查全时间窗, Python 过滤)
        snap_df = query_df(con, f"SELECT code, snapshot_time, Now, Before5MinNow, Amount FROM qd_stock_snapshot WHERE snapshot_time >= '{st}' AND snapshot_time <= '{et}' AND Amount > 0")
        intra_df = query_df(con, f"SELECT code, snapshot_time, MA5Value, fHSL, Zjl, ZAF, FCAmo FROM qd_stock_intraday WHERE snapshot_time >= '{st}' AND snapshot_time <= '{et}'")
        snap_df = snap_df[snap_df['code'].isin(strong_codes)].sort_values('snapshot_time').groupby('code', as_index=False).last()
        intra_df = intra_df[intra_df['code'].isin(strong_codes)].sort_values('snapshot_time').groupby('code', as_index=False).last()
        focus_df = snap_df.merge(intra_df, on='code', how='inner')
        print(f"focus_df: {len(focus_df)} 只")

        # 3. 板块评分 (补龙头涨幅/连板)
        linkage_scores, linkage_metrics = {}, {}
        for bc, info in block_info.items():
            bs = focus_df[focus_df['code'].isin(info['comp'])]
            leader = float(bs['ZAF'].max()) if not bs.empty else 0
            lb = max([ladder.get(c, 0) for c in info['comp']], default=0)
            linkage_scores[bc] = calc_linkage_score(info['main_net'], info['zt'], lb, leader, info['zt_full'])
            linkage_metrics[bc] = {'zt_count': info['zt'], 'main_net': info['main_net']}

        # 4. 票型分类
        daily_feat = _load_daily_features(con)
        lhb_feat = _load_lhb_features(con)
        cfg7 = k7_cfg()
        stock_types = {}
        for _, r in focus_df.iterrows():
            cls = classify_stock_type(r['code'], daily_feat.get(r['code']),
                {'hsl': float(r['fHSL']), 'lianb': 0, 'zjl': float(r['Zjl']), 'zangsu': 0},
                lhb_feat.get(r['code']), cfg7)
            cls['concept_heat'] = calc_concept_heat(r['code'], linkage_scores)
            stock_types[r['code']] = cls
        from collections import Counter
        td = Counter(v['type'] for v in stock_types.values())
        print(f"票型: 情绪{td['sentiment']}/趋势{td['trend']}/混合{td['mixed']}")

        # 5. 跑 p28
        ctx = CtxStub()
        ctx.snapshot_focus_df = focus_df
        ctx.linkage_scores = linkage_scores
        ctx.linkage_metrics = linkage_metrics
        ctx.linkage_thresholds = {'opportunity': 45.0}
        ctx.stock_types = stock_types

        print(f"\n{'='*70}\n🎯 p28 信号检测 (强势板块聚焦)\n{'='*70}")
        decisions = SectorFocusSignalStrategy().evaluate(ctx)

        if not decisions:
            print("无信号")
        else:
            buys = [d for d in decisions if d.action == 'buy']
            obs = [d for d in decisions if d.action == 'observe']
            print(f"\n🔴 立即行动 buy: {len(buys)} 条")
            for d in buys:
                print(f"   {get_stock_name(d.code)}({d.code}) 评分{d.score:.0f} | {d.reason}")
            print(f"\n🟡 关注埋伏 observe: {len(obs)} 条 (显示前12)")
            for d in obs[:12]:
                print(f"   {get_stock_name(d.code)}({d.code}) 评分{d.score:.0f} | {d.reason}")
        print(f"\n{'='*70}\n✅ 完成")
    finally:
        con.close()


if __name__ == '__main__':
    main()
