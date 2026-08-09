"""testv10.2 主链路数据导出 Excel — 跑一轮 funnel, 各数据源导出多 sheet

脚本路径: K:/QuestDB_test/testv10.2/export_main_data.py
用途: 盘外/盘中跑一轮完整主链路, 把 pool/drilled/sentiment/rotation 数据导出 xlsx,
      供人工检查内容 (字段/量级/口径)。dry-run 禁推。
用法: python testv10.2/export_main_data.py [--out path.xlsx]
"""

import bootstrap
bootstrap.ensure_paths()

import argparse  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from lib.tq_client import init, close  # noqa: E402
import settings as cfg  # noqa: E402
import mapping_store as ms_mod  # noqa: E402
from meso_radar import MesoRadar, pool_boards  # noqa: E402
from board_pool import BoardPool  # noqa: E402
import ticker as tk  # noqa: E402
from sentiment_monitor import SentimentMonitor  # noqa: E402
import radar_main  # noqa: E402


def main(out_path: str) -> None:
    ms = ms_mod.MappingStore(cfg.MAPPING_PARQUET)
    radar = MesoRadar(ms, cfg.MONITOR_LEVELS)
    pool = BoardPool()
    sentiment = SentimentMonitor(ms, pub=None, dry_run=True)
    init()
    now = datetime.now()
    t0 = now.timestamp()
    try:
        # 1. 完整 funnel: meso 扫描 → 入池 → 全A预筛 → 钻取
        rows = radar.scan()
        entered = pool.update(pool_boards(rows), 0)
        hot = pool.hot_codes()
        df = tk.scan_universe()
        pct_map = dict(zip(df['code'], df['pct']))
        zt_map = {r['code']: r.get('ZTGPNum', 0) for r in rows}
        candidates = radar_main.select_drill_candidates(hot, ms, pct_map, zt_map)
        drilled = tk.drill_stocks(candidates, df=df)

        # 中间层计数 (漏斗: 扫描→探照灯命中→入池→成分股候选→钻取)
        n_scanned = len(rows)                       # 扫描板块数 (578)
        n_light_hit = sum(1 for r in rows if r.get('searchlights'))  # 命中探照灯 (47)
        n_pool = len(pool.boards)                   # 池内板块 (30)
        n_candidates = len(candidates)              # 成分股候选 (去重前, 入钻取)
        n_drilled = len(drilled)                    # 钻取成功 (152)
        # 单调用/降级计数 (钻取日志统计, 用钻取结果反查无法区分, 从 drill 日志无法; 用 hot 板成分股粗估略过)
        # 2. 大盘情绪 (复用数据_provider bundle)
        bundle = {'stage': cfg.get_stage(now), 'df': df,
                  'sentiment_raw': __import__('sentiment_fetcher').fetch_sentiment_raw(df)}
        sresult = sentiment.compute(bundle, rows)

        # 3. 汇总 sheets
        # Sheet1 板块池 (状态机)
        pool_rows = []
        for st in pool.snapshot():
            members = ms.stocks_of(st.code)
            # 板块成分股 (取监控级别内的, 列出前 5 名 + 总数)
            member_names = [ms.stock_name(c) for c in list(members)[:5]]
            pool_rows.append({
                '板块代码': st.code, '板块名称': st.name, '状态': st.state,
                '动能分': round(st.score, 1), '板块涨幅%': round(st.zaf, 2),
                '涨停家数': st.zt_num, '量比': round(st.flianb, 2),
                '涨速': round(st.velocity, 2), '排名': st.rank,
                '最佳排名': st.peak_rank, '连续miss': st.miss_count,
                '在池轮数': st.rounds_in,
                '命中探照灯': '/'.join(sorted(st.searchlights)),
                '成分股数': len(members),
                '成分股示例': '、'.join(member_names),
            })
        # Sheet2 钻取个股 (因子明细)
        drill_rows = []
        for c, d in drilled.items():
            drill_rows.append({
                '个股代码': c, '个股名称': ms.stock_name(c),
                '涨幅%': round(d.get('ZAF', 0), 2), '封单额(万)': round(d.get('FCAmo', 0)),
                '量比': round(d.get('fLianB', 0), 2), '主力净流入(万)': round(d.get('Zjl_HB', 0)),
                '涨停价': round(d.get('ZTPrice', 0), 2), '现价': round(d.get('Now', 0), 2),
                '距高位置': round(d.get('pos_ratio', 0), 3), '涨停比例': round(d.get('limit_pct', 0), 3),
                '52周最高': round(d.get('HisHigh', 0), 2), '降级': d.get('degraded', False),
                '所属板块': '、'.join(ms.board_name(b) for b in
                                     sorted(ms.boards_of(c))[:5]),
            })
        drill_rows.sort(key=lambda r: r['涨幅%'], reverse=True)
        # Sheet3 大盘情绪
        sent_row = [{
            '综合分': sresult['score'], '档位': sresult['label'], '趋势': sresult['trend'],
            '上涨家数': sresult['up_home'], '下跌家数': sresult['down_home'],
            '涨停数': sresult['zt_cnt'], '跌停数': sresult['dt_cnt'], '炸板数': sresult['blasted'],
            '封板率%': sresult['fbl'], '封成比': sresult['avg_fcb'],
            '主力净流(万)': sresult['main_net'], '今日成交(万)': sresult['amount_today'],
            '最高连板': sresult['max_lb'], '昨连板表现%': sresult['cont_chg'],
            '亏钱比': sresult['loss_ratio'], '结论': sresult['conclusion'],
        }]
        # Sheet4 meso 板块扫描 (578 全量, 命中探照灯标记)
        meso_rows = [{'板块代码': r['code'], '板块名称': r['name'], '级别': r['level'],
                      '涨幅%': round(r['ZAF'], 2), '涨停家数': int(r['ZTGPNum']),
                      '量比': round(r['fLianB'], 2), '涨速': round(r['velocity'], 2),
                      '动能分': round(r['score'], 1),
                      '命中探照灯': '/'.join(sorted(r['searchlights']))}
                     for r in rows]
        meso_rows.sort(key=lambda r: r['动能分'], reverse=True)

        with pd.ExcelWriter(out_path, engine='openpyxl') as w:
            pd.DataFrame(pool_rows).to_excel(w, sheet_name='板块池', index=False)
            pd.DataFrame(drill_rows).to_excel(w, sheet_name='钻取个股', index=False)
            pd.DataFrame(sent_row).to_excel(w, sheet_name='大盘情绪', index=False)
            pd.DataFrame(meso_rows).to_excel(w, sheet_name='meso板块扫描', index=False)
            pd.DataFrame([
                {'层级': '①扫描板块', '指标': '扫描全部板块数', '值': n_scanned,
                 '说明': 'monitor 级别 (概念+三级), 逐板块 get_more_info+snapshot'},
                {'层级': '②探照灯', '指标': '命中探照灯板块', '值': n_light_hit,
                 '说明': '6探照灯各TopN并集: 涨幅/涨停/涨速/量比/振幅/跌幅'},
                {'层级': '③入池', '指标': '池内板块', '值': n_pool,
                 '说明': '状态机硬上限30, 按动能分+状态优先级裁掉低分'},
                {'层级': '④成分股候选', '指标': '候选股数(去重前)', '值': n_candidates,
                 '说明': '每入池板取成分股Top8/热点Top15, 全局去重'},
                {'层级': '④成分股候选', '指标': '钻取成功个股', '值': n_drilled,
                 '说明': '逐只 get_more_info 钻取因子 (涨停候选再+snapshot保Max)'},
                {'层级': '⑤全A', '指标': '全A个股数', '值': len(df),
                 '说明': 'get_pricevol 批量取, 只算 pct 供候选排序'},
                {'层级': '-', '指标': '总耗时(s)', '值': round(time.time() - t0, 1), '说明': ''},
            ]).to_excel(w, sheet_name='汇总', index=False)
        logger.info('导出完成: {} (池 {} / 钻取 {} / 情绪分 {})',
                    out_path, len(pool_rows), len(drill_rows), sresult['score'])
    finally:
        close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='导出主链路数据到 Excel')
    p.add_argument('--out', default=os.path.join('..', '导出_主链路数据.xlsx'), help='输出路径')
    a = p.parse_args()
    main(a.out)
