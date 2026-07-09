"""k4 深度情绪独立调度脚本

脚本路径: K:\\QuestDB_test\\runner\\k4_runner.py
用途: 5min/轮独立运行 k4 三个深度模块 (情绪 + 板块热力图 + 打板梯队) +
      飞书多维表格落盘, 与 intraday_loop 解耦, 由 scheduler 调度
执行时间: 盘中 (09:30-15:00) 每 5 分钟
频率: 5min/轮 (scheduler 控制)

解耦理由:
  - 之前 k4 挂在 intraday_loop 的 60s 块内, 计数器在 _run_rotation 函数属性上,
    跨进程不共享 → 重启后清零, 触发时机不稳定
  - 改成独立进程后, scheduler 在 09:30-15:00 间每 5 分钟启动一次
  - 进程退出 → scheduler 立即拉起下一个 5min, 心跳保活

k4 模块集成:
  - k4_sentiment        → qd_sentiment_deep + 飞书推送
  - k4_sector_heatmap   → qd_sector_heatmap + 飞书推送
  - k4_ladder_tracker   → qd_ladder_tracker + 飞书推送

飞书多维表格落盘:
  - 由 k4_sentiment / k4_sector_heatmap / k4_ladder_tracker 内部调用
  - 每日三张表: 情绪全景 / 板块梯队 / 打板梯队
  - 每 5min 一行
"""

import os
import sys
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger

from lib.qdb import connect


_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k4_runner_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')


def _run_k4_once(con):
    """一次 k4 全套: 深度情绪 + 板块热力图 + 打板梯队 + 飞书多维表格落盘

    Args:
        con: psycopg2 连接

    Returns:
        dict: 三个模块的结果摘要
    """
    summary = {}

    # Bitable token (统一获取一次)
    bitable_token = ''
    try:
        from feishu.config import BITABLE_TOKEN
        bitable_token = BITABLE_TOKEN
    except Exception:
        pass

    # 1. k4 深度情绪 (恐慌/贪婪指数 + 背离)
    try:
        import compute.k4_sentiment as k4
        r = k4.run(con)
        summary['_sentiment_full'] = r
        summary['k4_sentiment'] = {
            'pg_index': r.get('pg_index'),
            'divergence_count': r.get('divergence_count'),
            'turning_point': r.get('turning_point', {}).get('type') if r.get('turning_point') else None,
        }
        logger.info('k4_sentiment OK: PG={} 背离={} 拐点={}',
                    r.get('pg_index'), r.get('divergence_count'),
                    summary['k4_sentiment']['turning_point'] or '无')
        # Bitable 写入情绪全景
        if bitable_token and r:
            try:
                from feishu.bitable_writer import write_panorama_row
                write_panorama_row(bitable_token, r)
            except Exception as e:
                logger.warning('Bitable 情绪全景写入失败: {}', e)
    except Exception as e:
        logger.error('k4_sentiment 失败: {}', e)
        summary['k4_sentiment'] = {'error': str(e)[:100]}

    # 2. k4 板块热力图 (4 组 Top5 + 最强个股)
    try:
        import compute.k4_sector_heatmap as k4_heatmap
        r = k4_heatmap.run(con)
        summary['_heatmap_full'] = r
        ranks = sum(1 for k in ['industry_l1_ranking', 'industry_l2_ranking',
                                'industry_l3_ranking', 'concept_ranking']
                    if r.get(k))
        summary['k4_sector_heatmap'] = {'rankings': ranks}
        logger.info('k4_sector_heatmap OK: 4 组排行={}', ranks)
        # Bitable 写入板块梯队
        if bitable_token and r:
            try:
                from feishu.bitable_writer import write_heatmap_row
                write_heatmap_row(bitable_token, r)
            except Exception as e:
                logger.warning('Bitable 板块梯队写入失败: {}', e)
    except Exception as e:
        logger.error('k4_sector_heatmap 失败: {}', e)
        summary['k4_sector_heatmap'] = {'error': str(e)[:100]}

    # 3. k4 打板梯队 (连板全景 + 2进3 评分)
    try:
        import compute.k4_ladder_tracker as k4_ladder
        r = k4_ladder.run(con)
        summary['_ladder_full'] = r
        stats = r.get('stats', {})
        summary['k4_ladder_tracker'] = {
            'total_zt': stats.get('total_zt', 0),
            'candidates_2to3': stats.get('candidates_2to3', 0),
        }
        logger.info('k4_ladder_tracker OK: 连板={} 2进3={}',
                    stats.get('total_zt', 0), stats.get('candidates_2to3', 0))
        # Bitable 写入打板梯队
        if bitable_token and r:
            try:
                from feishu.bitable_writer import write_ladder_row
                write_ladder_row(bitable_token, r)
            except Exception as e:
                logger.warning('Bitable 打板梯队写入失败: {}', e)
    except Exception as e:
        logger.error('k4_ladder_tracker 失败: {}', e)
        summary['k4_ladder_tracker'] = {'error': str(e)[:100]}

    # ── 合并 k4 三模块推送为一条 ──
    _feishu_texts = []
    _r_sent = summary.get('_sentiment_full')
    if _r_sent:
        try:
            from compute.k4_sentiment import push_panoramic
            _t = push_panoramic(_r_sent)
            if _t:
                _feishu_texts.append(_t)
        except Exception as e:
            logger.warning('k4 全景推送生成失败: {}', e)
    _r_heat = summary.get('_heatmap_full')
    if _r_heat:
        try:
            from compute.k4_sector_heatmap import push_heatmap
            _t = push_heatmap(_r_heat)
            if _t:
                _feishu_texts.append(_t)
        except Exception as e:
            logger.warning('k4 热力推送生成失败: {}', e)
    _r_lad = summary.get('_ladder_full')
    if _r_lad:
        try:
            from compute.k4_ladder_tracker import push_ladder
            _t = push_ladder(_r_lad)
            if _t:
                _feishu_texts.append(_t)
        except Exception as e:
            logger.warning('k4 打板推送生成失败: {}', e)
    if _feishu_texts:
        _sep = chr(10) * 2 + '---' + chr(10) * 2
        _merged = _sep.join(_feishu_texts)
        from feishu import push_text
        push_text(_merged)
        logger.info('k4 合并推送: {} 个模块', len(_feishu_texts))

    return summary


def main():
    """k4_runner 入口

    设计: 由 scheduler 每 5 分钟启动一次, 跑完就退出。
    这样:
      - 进程失败不影响 intraday_loop
      - 心跳保活由 scheduler 做
      - 5min 窗口靠 scheduler 调度而非内部计数器
    """
    logger.info('===== k4_runner 启动 {} =====', datetime.now())

    con = None
    try:
        con = connect()
        summary = _run_k4_once(con)
        logger.info('===== k4_runner 完成: {} =====', summary)
    except Exception as e:
        logger.error('k4_runner 异常退出: {}', e)
    finally:
        if con is not None:
            con.close()


if __name__ == '__main__':
    main()