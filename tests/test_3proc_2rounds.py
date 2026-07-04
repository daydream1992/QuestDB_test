"""_test_3proc_2rounds: 多进程并行 3 接口(全场)跑 2 轮, 验证稳定性 + 耗时一致性"""
import os
import sys
import time
import json
from multiprocessing import Process, Queue
from pathlib import Path
from datetime import datetime
from loguru import logger

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\sys')

LOG_DIR = Path(__file__).resolve().parent / 'logs'
logger.add(LOG_DIR / f'test3p_2rounds_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')


def fetch_all_tdx_codes():
    """取全市场代码 (tdx 格式)"""
    from tqcenter import tq
    tq.initialize(__file__)
    try:
        sectors = tq.get_sector_list() or []
        all_set = set()
        for s in sectors:
            cs = tq.get_stock_list_in_sector(s) or []
            all_set.update(cs)
        all_set.update(sectors)
        def to_tdx(c):
            if c.endswith('.SH') or c.endswith('.SZ'): return c
            if c.startswith(('88', '9')): return c
            return f'{c}.SH' if c.startswith('6') else f'{c}.SZ'
        return [to_tdx(c) for c in sorted(all_set)]
    finally:
        try: tq.close()
        except: pass


CODES_CACHE = []  # 进程间共享代码列表 (主进程准备好, 子进程直接接收)


def worker_pricevol(q, round_id, codes):
    """全场 get_pricevol"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        d = tq.get_pricevol(stock_list=codes)
        elapsed = time.time() - t0
        q.put(('pricevol', round_id, 'OK', len(d), elapsed))
    except Exception as e:
        q.put(('pricevol', round_id, f'ERR: {e}', 0, time.time() - t0))
    finally:
        try: tq.close()
        except: pass


def worker_market_data(q, round_id, codes):
    """全场 get_market_data(1d, count=1)"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        d = tq.get_market_data(stock_list=codes, period='1d', count=1)
        elapsed = time.time() - t0
        q.put(('market_data', round_id, 'OK', len(d), elapsed))
    except Exception as e:
        q.put(('market_data', round_id, f'ERR: {e}', 0, time.time() - t0))
    finally:
        try: tq.close()
        except: pass


def worker_more_info(q, round_id, codes):
    """全场 get_more_info 88 字段"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        ok = 0
        for c in codes:
            try:
                d = tq.get_more_info(c, field_list=[])
                if d: ok += 1
            except: pass
        elapsed = time.time() - t0
        q.put(('more_info', round_id, 'OK', ok, elapsed))
    except Exception as e:
        q.put(('more_info', round_id, f'ERR: {e}', 0, time.time() - t0))
    finally:
        try: tq.close()
        except: pass


def run_one_round(round_id, codes, q):
    """跑 1 轮 3 进程并行"""
    t_round = time.time()
    procs = [
        Process(target=worker_pricevol, args=(q, round_id, codes)),
        Process(target=worker_market_data, args=(q, round_id, codes)),
        Process(target=worker_more_info, args=(q, round_id, codes)),
    ]
    logger.info(f'=== Round {round_id} 启动 3 子进程 ===')
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=180)
        if p.is_alive():
            logger.warning(f'PID {p.pid} 超时, 强制结束')
            p.terminate()
            p.join()
    return time.time() - t_round


def main():
    # 1. 主进程准备代码列表
    logger.info('主进程取全市场代码...')
    codes = fetch_all_tdx_codes()
    logger.info(f'全市场 {len(codes)} 只')

    q = Queue()
    rounds = []
    for r in [1, 2]:
        elapsed = run_one_round(r, codes, q)
        rounds.append(elapsed)
        logger.info(f'=== Round {r} 总耗时: {elapsed:.2f}s ===')
        if r < 2:
            logger.info('等待 3s 后跑下一轮...')
            time.sleep(3)

    # 收集结果
    results = []
    while not q.empty():
        results.append(q.get())

    # 汇总
    logger.info('=' * 60)
    logger.info('=== 2 轮完整入场测试结果 ===')
    for r in results:
        logger.info(f'  Round {r[1]} | {r[0]:<13s} | {r[2]:<5s} | count={r[3]:<5d} | 耗时 {r[4]:.2f}s')
    logger.info('---')
    logger.info(f'Round 1 总耗时: {rounds[0]:.2f}s')
    logger.info(f'Round 2 总耗时: {rounds[1]:.2f}s')
    logger.info(f'2 轮平均: {(rounds[0]+rounds[1])/2:.2f}s')
    logger.info(f'2 轮差异: {abs(rounds[1]-rounds[0]):.2f}s ({abs(rounds[1]-rounds[0])/rounds[0]*100:.1f}%)')


if __name__ == '__main__':
    main()
