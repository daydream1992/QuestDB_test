"""票型分类器测试 (用 2026-07-09 历史数据)

验证: 强势板块成分股的分类分布是否合理
  - 情绪票应为小盘高换手游资票
  - 趋势票应为大盘机构票
"""

import sys
import os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from lib.qdb import connect, query_df
from lib.relation_graph import load_from_json, get_sector_stocks, _sector_meta
from compute.k7_stock_type import (
    classify_stock_type, calc_concept_heat, _load_config,
    _load_daily_features, _load_lhb_features,
)


def main():
    cfg = _load_config()
    load_from_json()

    con = connect()
    try:
        daily_feat = _load_daily_features(con)
        lhb_feat = _load_lhb_features(con)

        # 用 7月9日 14:52 的 intraday 数据
        start_time = '2026-07-09T14:47:10'
        end_time = '2026-07-09T14:57:10'
        df = query_df(con, f"""
            SELECT code, snapshot_time, fHSL, fLianB, Zjl, vzangsu
            FROM qd_stock_intraday
            WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
        """)
        df = df.sort_values('snapshot_time').groupby('code', as_index=False).last()
        intraday_feat = {r['code']: {
            'hsl': float(r['fHSL']) if r['fHSL'] == r['fHSL'] else 0,
            'lianb': float(r['fLianB']) if r['fLianB'] == r['fLianB'] else 0,
            'zjl': float(r['Zjl']) if r['Zjl'] == r['Zjl'] else 0,
            'zangsu': float(r['vzangsu']) if r['vzangsu'] == r['vzangsu'] else 0,
        } for _, r in df.iterrows()}
        print(f"intraday 特征: {len(intraday_feat)} 只")

        # 模拟 linkage_scores: 用 7月9日 板块数据算 (简化版)
        # 取资金流 Top 板块作为强势板块
        flow_df = query_df(con, f"""
            SELECT code, main_net FROM qd_sector_flow
            WHERE flow_time >= '{start_time}' AND flow_time <= '{end_time}'
            ORDER BY main_net DESC LIMIT 20
        """)
        # 简化评分: 给这些板块赋分 (用于概念热度)
        linkage_scores = {}
        for i, (_, r) in enumerate(flow_df.iterrows()):
            linkage_scores[r['code']] = 50.0 + (len(flow_df) - i)

        # 收集成分股
        target_codes = set()
        for block_code in list(linkage_scores.keys())[:10]:
            stocks = get_sector_stocks(block_code)
            for s in stocks:
                target_codes.add(s.get('code', ''))
        print(f"强势板块成分股: {len(target_codes)} 只")

        # 分类
        result = {}
        for code in target_codes:
            if not code or code not in intraday_feat:
                continue
            cls = classify_stock_type(
                code,
                daily_feat.get(code),
                intraday_feat.get(code),
                lhb_feat.get(code),
                cfg,
            )
            cls['concept_heat'] = calc_concept_heat(code, linkage_scores)
            result[code] = cls

        # 统计
        from collections import Counter
        type_dist = Counter(v['type'] for v in result.values())
        print(f"\n{'='*70}")
        print(f"分类统计: 情绪{type_dist['sentiment']} / 趋势{type_dist['trend']} / 混合{type_dist['mixed']}")
        print(f"{'='*70}")

        # 各类样例 (带股票名称)
        from lib.relation_graph import get_stock_name
        for t in ('sentiment', 'trend', 'mixed'):
            samples = sorted(
                [(c, v) for c, v in result.items() if v['type'] == t],
                key=lambda x: -(x[1]['sentiment_score'] + x[1]['trend_score'])
            )[:8]
            print(f"\n=== {t} 样例 ===")
            for code, v in samples:
                name = get_stock_name(code)
                d = daily_feat.get(code, {})
                i = intraday_feat.get(code, {})
                l = lhb_feat.get(code, {})
                lhb_tag = ''
                if l:
                    tags = []
                    if l.get('has_hotmoney'): tags.append('游资')
                    if l.get('has_institution'): tags.append('机构')
                    if l.get('has_north'): tags.append('北向')
                    lhb_tag = '[' + '/'.join(tags) + ']' if tags else '[龙虎榜]'
                print(f"  {name}({code}) s={v['sentiment_score']:.0f} t={v['trend_score']:.0f} "
                      f"热度={v['concept_heat']:.0f} 市值={d.get('ltsz',0):.0f}亿 "
                      f"换手={i.get('hsl',0):.1f}% 量比={i.get('lianb',0):.1f} "
                      f"Zjl={i.get('zjl',0)/1e4:.2f}亿 {lhb_tag}")

        print(f"\n{'='*70}")
        print(f"✅ 票型分类测试完成")
    finally:
        con.close()


if __name__ == '__main__':
    main()
