"""daily_summary: 收盘复盘报告自动生成

用途: daily_close 末尾调用, 生成当日复盘汇总并推飞书
"""
import os
import sys
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from lib.qdb import connect, query_df, cutoff
from loguru import logger
from feishu import flush_pending_bucket, build_review_card, _send, push_text


def _split_review_sections(lines):
    """把 daily_summary 的 lines 按分区标题拆成 4 块

    分区标题格式: '── 情绪变化 ──' / '── 数据入库 ──' / '── 策略产出 ──' / '── 异常告警 ──'
    """
    sections = {'emotion': '', 'data': '', 'strategy': '', 'alert': ''}
    key_map = {
        '情绪变化': 'emotion', '情绪': 'emotion',
        '数据入库': 'data', '入库': 'data',
        '策略产出': 'strategy', '策略': 'strategy',
        '异常告警': 'alert', '异常': 'alert',
    }
    current_key = None
    current_lines = []
    for line in lines:
        matched = False
        for keyword, key in key_map.items():
            if keyword in line and line.strip().startswith('──'):
                if current_key:
                    sections[current_key] = '\n'.join(current_lines)
                current_key = key
                current_lines = [line]
                matched = True
                break
        if not matched and current_key:
            current_lines.append(line)
    if current_key:
        sections[current_key] = '\n'.join(current_lines)
    return sections


def run():
    """拉当日全链路数据, 生成复盘汇总, 推飞书"""
    # 刷出盘中未推送的聚合决策桶
    try:
        flush_pending_bucket()
    except Exception as e:
        logger.warning('close flush 桶失败: {}', e)
    con = connect()
    try:
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')

        lines = []
        lines.append(f'📊 {date_str} 收盘复盘')
        lines.append('')

        # ── 1. 情绪走势 ──
        try:
            df = query_df(con,
                'SELECT snapshot_time, emotion, zt_cnt, break_cnt, fbl, udr, up_cnt, down_cnt '
                'FROM qd_sentiment_snapshot_min ORDER BY snapshot_time')
            if not df.empty:
                first = df.iloc[0]
                last = df.iloc[-1]
                lines.append(f'── 情绪变化 ──')
                lines.append(f'  开盘: {first["emotion"]} (涨停{first["zt_cnt"]} 涨跌比{first["udr"]:.2f})')
                lines.append(f'  收盘: {last["emotion"]} (涨停{last["zt_cnt"]} 涨跌比{last["udr"]:.2f})')
                lines.append(f'  涨家: {first["up_cnt"]}→{last["up_cnt"]}')
                lines.append(f'  跌家: {first["down_cnt"]}→{last["down_cnt"]}')
                worst_fbl = df.loc[df['fbl'].idxmin()]
                lines.append(f'  最差封板率: {worst_fbl["fbl"]:.0f}% (峰值炸板{worst_fbl["break_cnt"]}只)')
                lines.append('')
        except Exception as e:
            logger.warning('读情绪数据失败: {}', e)

        # ── 2. 数据入库规模 ──
        try:
            tables = [
                ('qd_stock_snapshot', '快照'), ('qd_stock_intraday', '主力资金'),
                ('qd_pricevol', '价量'), ('qd_kline_5m', '5mK线'),
                ('qd_indicators', '技术指标'), ('qd_signals', '原子信号'),
                ('qd_decisions', '策略决策'), ('qd_money_flow', '个股资金'),
                ('qd_sector_flow', '板块资金'), ('qd_resonance', '共振分析'),
            ]
            lines.append('── 数据入库 ──')
            for tbl, label in tables:
                df = query_df(con, f'SELECT count(*) as c FROM {tbl}')
                cnt = df['c'].iloc[0]
                lines.append(f'  {label}({tbl}): {cnt:,} 行')
            lines.append('')
        except Exception as e:
            logger.warning('读入库数据失败: {}', e)

        # ── 3. 策略产出 ──
        try:
            df = query_df(con,
                'SELECT strategy_name, action, count(*) as cnt '
                'FROM qd_decisions GROUP BY strategy_name, action ORDER BY cnt DESC')
            if not df.empty:
                lines.append(f'── 策略产出 ({len(df)} 条决策) ──')
                for _, r in df.iterrows():
                    lines.append(f'  {r["strategy_name"]}: {r["action"]} × {r["cnt"]}')
                lines.append('')

            df_all = query_df(con, 'SELECT strategy_name, action, count(*) as cnt FROM qd_decisions GROUP BY strategy_name')
            active = set(df_all['strategy_name'].tolist())
            from strategy.registry import StrategyRegistry
            all_strategies = StrategyRegistry.get_all() if hasattr(StrategyRegistry, 'get_all') else []
            silent = [s.name for s in all_strategies if s.name not in active]
            if silent:
                lines.append(f'  静默策略({len(silent)}): {", ".join(silent)}')
                lines.append('')
        except Exception as e:
            logger.warning('读策略产出失败: {}', e)

        # ── 4. 信号概览 ──
        try:
            df = query_df(con,
                'SELECT signal_type, count(*) as cnt FROM qd_signals GROUP BY signal_type ORDER BY cnt DESC')
            if not df.empty:
                lines.append(f'── 原子信号 ({df["cnt"].sum()} 条) ──')
                for _, r in df.iterrows():
                    lines.append(f'  {r["signal_type"]}: {r["cnt"]}')
                lines.append('')
        except Exception as e:
            logger.warning('读信号数据失败: {}', e)

        # ── 5. 零产出模块告警 ──
        zero = []
        for tbl, label in [('qd_intraday_event', '异动事件'), ('qd_big_order', '大单'),
                          ('qd_stock_gpjy', '隔夜大宗'), ('qd_lhb_detail', '龙虎榜')]:
            try:
                df = query_df(con, f'SELECT count(*) as c FROM {tbl}')
                if df['c'].iloc[0] == 0:
                    zero.append(label)
            except Exception:
                pass
        if zero:
            lines.append(f'⚠️ 零产出模块: {", ".join(zero)}')
            lines.append('')

        # ── 6. 当日修复/异常 ──
        lines.append('── 异常告警 ──')
        log_file = os.path.join(_PROJ_ROOT, 'logs', f'runner_intraday_loop_{now.strftime("%Y%m%d")}.log')
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            err_count = content.count('ERROR')
            warn_count = content.count('WARNING')
            lines.append(f'  日志错误: {err_count} ERROR / {warn_count} WARNING')
            key_alerts = []
            if '字段护栏' in content:
                key_alerts.append('字段护栏拦截')
            if '飞书频控拦截' in content:
                key_alerts.append('飞书频控')
            if 'k1 本轮无新增指标' in content:
                key_alerts.append('k1指标窗口不足')
            if '变盘' in content:
                key_alerts.append('变盘信号触发')
            if key_alerts:
                lines.append(f'  关键标记: {" | ".join(key_alerts)}')
        lines.append('')
        lines.append(f'—— {now.strftime("%H:%M")} 自动复盘 --')

        text = '\n'.join(lines)
        logger.info('复盘汇总:\n{}', text)

        # 构造 k4 数据 (收盘复盘卡片扩展分区)
        k4_data = {}
        try:
            con_k4 = connect()
            try:
                # 情绪总结
                df_k4 = query_df(con_k4,
                    'SELECT pg_index, pg_signal, zt_cnt, dt_cnt, udr, main_net '
                    'FROM qd_sentiment_deep ORDER BY snapshot_time DESC LIMIT 1')
                if not df_k4.empty:
                    r = df_k4.iloc[0]
                    k4_data['sentiment'] = (
                        f'┄ 情绪结语 ┄\n'
                        f'  PG指数: {r.get("pg_index","-")} {r.get("pg_signal","")}\n'
                        f'  收市涨跌: {r.get("zt_cnt",0)}家涨停 / {r.get("dt_cnt",0)}家跌停\n'
                        f'  涨跌比: {r.get("udr","-")}'
                    )

                # 板块热力
                df_h = query_df(con_k4,
                    "SELECT industry_l1_ranking, industry_l2_ranking, concept_ranking "
                    "FROM qd_sector_heatmap ORDER BY snapshot_time DESC LIMIT 1")
                if not df_h.empty:
                    try:
                        import json
                        l1 = json.loads(df_h.iloc[0].get('industry_l1_ranking', '[]'))[:3]
                        concept = json.loads(df_h.iloc[0].get('concept_ranking', '[]'))[:3]
                        lines_h = ['┄ 最强板块收盘 ┄']
                        if l1:
                            lines_h.append(f'  行业: {" | ".join(f"{s.get("name","")}{s.get("zaf",""):+.1f}" for s in l1)}')
                        if concept:
                            lines_h.append(f'  概念: {" | ".join(f"{s.get("name","")}{s.get("zaf",""):+.1f}" for s in concept)}')
                        k4_data['heatmap'] = '\n'.join(lines_h)
                    except Exception:
                        pass

                # 打板梯队
                df_l = query_df(con_k4,
                    "SELECT stats FROM qd_ladder_tracker ORDER BY snapshot_time DESC LIMIT 1")
                if not df_l.empty:
                    try:
                        stats = json.loads(df_l.iloc[0].get('stats', '{}'))
                        lines_l = ['┄ 打板梯队收盘 ┄']
                        lines_l.append(f'  首板:{stats.get("total_1b",0)} | 2板:{stats.get("total_2b",0)} | 3板:{stats.get("total_3b",0)} | 4板:{stats.get("total_4b",0)} | 5板+:{stats.get("total_5b_plus",0)}')
                        lines_l.append(f'  2进3候选: {stats.get("candidates_2to3",0)} 只')
                        k4_data['ladder'] = '\n'.join(lines_l)
                    except Exception:
                        pass
            finally:
                con_k4.close()
        except Exception as e:
            logger.warning('读取 k4 复盘数据失败: {}', e)

        try:
            # 构造 T4 复盘卡片 (含 k4 扩展)
            sections = _split_review_sections(lines)
            card = build_review_card(sections, k4_data=k4_data)
            ok = _send({'msg_type': 'interactive', 'card': card})
            logger.info('复盘推送(卡片): {}', ok)
        except Exception as e:
            logger.warning('复盘卡片推送失败, 降级纯文本: {}', e)
            try:
                push_text(text)   # 降级: 卡片失败用纯文本兜底
            except Exception as e2:
                logger.warning('复盘纯文本推送也失败: {}', e2)

        return text
    finally:
        con.close()
