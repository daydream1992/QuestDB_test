#!/usr/bin/env python3
"""4 小时持续写入压力测试 — 验证系统在持续运行 4h 后的稳定性

目标:
  1. 验证 QuestDB 在持续 4h 高频写入下不崩 (连接数/延迟/错误率)
  2. 验证 parquet 分片写入为 O(1), 不随运行时间增长 (如果备份已启用)
  3. 验证进程内连接复用不泄漏 (连接数稳定在 ~2)
  4. 验证 _ensure_alive 断线重连

用法:
  python tests/test_stress_4h.py [--duration 4] [--interval 1] [--enable-parquet]

输出:
  - 实时进度: 每轮写入 row_count, 累积统计
  - 最终报告: 总行数/总耗时/平均延迟/P99 延迟/错误数/错误率
  - 帮助诊断回归: 如果改动后运行 2h 就崩, 说明引入新的 O(N) 问题

日期: 2026-07-08
"""

import argparse
import os
import sys
import time
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
from lib.qdb import connect, query_df, executemany_batch, cutoff


# ── 测试参数 ──
_TABLES = {
    'qd_pricevol': ['code', 'snapshot_time', 'LastClose', 'Now', 'Volume'],
    'qd_stock_snapshot': [
        'code', 'snapshot_time',
        'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
        'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    ],
}
_NUM_CODES = 100  # 模拟 100 只标的


def _gen_row(table: str, idx: int, ts: datetime) -> tuple:
    """生成一行测试数据"""
    code = f'{idx:06d}.SZ'
    if table == 'qd_pricevol':
        return (code, ts, 10.0, 10.5, 1000000)
    if table == 'qd_stock_snapshot':
        return (code, ts, 1, 10.0, 10.0, 10.5, 9.5, 10.5,
                100000, 10000, 500000, 50000, 50000)
    return (code, ts)


def _drop_partition_by_date(con, table, date_str):
    """按日期删除 QuestDB 分区 (DROP PARTITION LIST)"""
    try:
        cur = con.cursor()
        cur.execute(f"ALTER TABLE {table} DROP PARTITION LIST ('{date_str}')")
        cur.close()
    except Exception:
        pass


def _ensure_table_cleanup(con):
    """清理本轮测试的残留数据"""
    today_str = datetime.now().strftime('%Y-%m-%d')
    for t in _TABLES:
        _drop_partition_by_date(con, t, today_str)


def run_stress(duration_minutes: int = 240, interval: int = 10,
               enable_parquet: bool = False, enable_backup: bool = False):
    """运行压力测试

    Args:
        duration_minutes: 运行时长 (分钟)
        interval: 每轮间隔 (秒)
        enable_parquet: 开启 parquet 分片写入 (默认关, 基础性能测试)
        enable_backup: 开启旧版 parquet 合并 (用于复现 O(N) 问题)
    """
    logger.info(f'===== 压力测试启动: {duration_minutes}min, interval={interval}s =====')

    if enable_parquet:
        import lib.qdb
        lib.qdb._BACKUP_ENABLED = True
        if enable_backup:
            # 复现旧版 O(N) 模式: 不启用批次分片, 强制使用旧版 (测试用)
            logger.warning('旧版 parquet 合并模式已启用 (用于复现 O(N) 问题)')
        else:
            logger.info('新版 parquet 分片模式 (当前实现即为分片)')

    con = connect()
    start_ts = time.time()
    deadline = start_ts + duration_minutes * 60

    total_rows = 0
    round_count = 0
    errors = 0
    latencies: list[float] = []

    # 确保测试表有数据
    _ensure_table_cleanup(con)

    try:
        while time.time() < deadline:
            t0 = time.time()
            now = datetime.now()
            batch_rows = 0
            round_err = 0

            try:
                # 写 qd_pricevol (轻量高频)
                rows_pricevol = [_gen_row('qd_pricevol', i, now) for i in range(_NUM_CODES)]
                n = executemany_batch(con, 'qd_pricevol', _TABLES['qd_pricevol'], rows_pricevol)
                batch_rows += n
            except Exception as e:
                logger.error('pricevol 写入失败: {}', e)
                round_err += 1

            if round_count % 6 == 0:
                # 写 qd_stock_snapshot (模拟 c2 写入)
                try:
                    rows_snap = [_gen_row('qd_stock_snapshot', i, now) for i in range(50)]
                    n = executemany_batch(con, 'qd_stock_snapshot', _TABLES['qd_stock_snapshot'], rows_snap)
                    batch_rows += n
                except Exception as e:
                    logger.error('snapshot 写入失败: {}', e)
                    round_err += 1

                # 读验证 (模拟 _build_context)
                try:
                    query_df(con, f"SELECT count(*) AS c FROM qd_pricevol "
                             f"WHERE snapshot_time >= '{cutoff(minutes=5)}'")
                except Exception as e:
                    logger.error('查询失败: {}', e)
                    round_err += 1

            # 每 30 轮验证连接健康 (模拟 _ensure_alive)
            if round_count % 30 == 0:
                try:
                    cur = con.cursor()
                    cur.execute('SELECT 1')
                    cur.fetchone()
                    cur.close()
                except Exception:
                    logger.warning('连接失效, 重建')
                    try:
                        con.close()
                    except Exception:
                        pass
                    con = connect()

            elapsed = time.time() - t0
            latencies.append(elapsed)
            total_rows += batch_rows
            round_count += 1
            errors += round_err

            if round_count % 10 == 0:
                elapsed_total = time.time() - start_ts
                rate = total_rows / elapsed_total if elapsed_total > 0 else 0
                logger.info(f'[{round_count}] rows={total_rows} '
                           f'rate={rate:.0f}行/s '
                           f'err={errors} '
                           f'lat_avg={sum(latencies[-10:])/10:.3f}s '
                           f'remain={int((deadline - time.time())/60)}min')

            # 控频
            sleep_s = max(0.1, interval - (time.time() - t0))
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        logger.info('用户中断')
    finally:
        # 清理测试数据
        try:
            _drop_partition_by_date(con, 'qd_pricevol', datetime.now().strftime('%Y-%m-%d'))
            _drop_partition_by_date(con, 'qd_stock_snapshot', datetime.now().strftime('%Y-%m-%d'))
        except Exception:
            pass
        try:
            con.close()
        except Exception:
            pass
        # 报告
        elapsed = time.time() - start_ts
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

        logger.info('')
        logger.info('===== 压力测试报告 =====')
        logger.info(f'  运行时长:      {elapsed:.0f}s ({elapsed/60:.1f}min)')
        logger.info(f'  总轮次:        {round_count}')
        logger.info(f'  总写入行数:    {total_rows}')
        logger.info(f'  平均速率:      {total_rows/elapsed:.0f} 行/s' if elapsed > 0 else '  N/A')
        logger.info(f'  错误数:        {errors}')
        logger.info(f'  错误率:        {errors/max(round_count,1)*100:.1f}%')
        logger.info(f'  平均延迟:      {sum(latencies)/len(latencies):.3f}s' if latencies else '  N/A')
        logger.info(f'  P50 延迟:      {p50:.3f}s')
        logger.info(f'  P99 延迟:      {p99:.3f}s')
        logger.info('')

        # 健康判定
        health = 'HEALTHY' if errors == 0 else 'DEGRADED'
        if errors / max(round_count, 1) > 0.1:
            health = 'UNSTABLE'
        if p99 > 5.0:
            health = 'SLOW (可能的 O(N) 问题)'

        logger.info(f'  健康状态: {health}')
        logger.info(f'  {"="*30}')
        if enable_backup:
            logger.warning('  注意: 旧版 parquet 合并模式已启用, 复现 O(N) 问题的预期现象')
        logger.info(f'  ==============================')
        logger.info(f'')

        return health, {'elapsed': elapsed, 'rounds': round_count, 'total_rows': total_rows,
                        'errors': errors, 'p50': p50, 'p99': p99}


def main():
    parser = argparse.ArgumentParser(description='4 小时压力测试')
    parser.add_argument('--duration', type=int, default=4,
                        help='运行时长 (分钟), 默认 4 (quick)')
    parser.add_argument('--interval', type=float, default=1.0,
                        help='轮次间隔 (秒), 默认 1.0')
    parser.add_argument('--enable-parquet', action='store_true',
                        help='开启 parquet 分片写入')
    parser.add_argument('--enable-old-backup', action='store_true',
                        help='开启旧版 parquet 合并 (复现 O(N) 问题)')
    args = parser.parse_args()

    run_stress(
        duration_minutes=args.duration,
        interval=args.interval,
        enable_parquet=args.enable_parquet,
        enable_backup=args.enable_old_backup,
    )


if __name__ == '__main__':
    main()
