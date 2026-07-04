"""_test_3proc: 多进程并行 3 接口测试 (验证 tqcenter COM 跨进程是否安全)

启 3 个子进程, 同时调 3 个不同 API, 看是否都能完成。
"""
import os
import sys
import time
import json
from multiprocessing import Process, Queue
from pathlib import Path
from datetime import datetime

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\sys')


def worker_get_pricevol(q, label):
    """进程 1: get_pricevol 全场"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        # 取全市场代码
        sectors = tq.get_sector_list() or []
        all_set = set()
        for s in sectors:
            cs = tq.get_stock_list_in_sector(s) or []
            all_set.update(cs)
        all_set.update(sectors)
        codes = sorted(all_set)
        # 转 tdx
        def to_tdx(c):
            if c.endswith('.SH') or c.endswith('.SZ'): return c
            if c.startswith(('88', '9')): return c
            return f'{c}.SH' if c.startswith('6') else f'{c}.SZ'
        codes = [to_tdx(c) for c in codes]
        d = tq.get_pricevol(stock_list=codes)
        elapsed = time.time() - t0
        q.put((label, 'OK', len(d), elapsed))
    except Exception as e:
        elapsed = time.time() - t0
        q.put((label, f'ERR: {e}', 0, elapsed))
    finally:
        try: tq.close()
        except: pass


def worker_get_market_data(q, label):
    """进程 2: get_market_data K线"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        # 取全市场代码
        sectors = tq.get_sector_list() or []
        all_set = set()
        for s in sectors:
            cs = tq.get_stock_list_in_sector(s) or []
            all_set.update(cs)
        all_set.update(sectors)
        codes = sorted(all_set)[:50]  # 限 50 只避免太慢
        d = tq.get_market_data(stock_list=codes, period='1d', count=1)
        elapsed = time.time() - t0
        q.put((label, 'OK', len(d), elapsed))
    except Exception as e:
        elapsed = time.time() - t0
        q.put((label, f'ERR: {e}', 0, elapsed))
    finally:
        try: tq.close()
        except: pass


def worker_get_more_info(q, label):
    """进程 3: get_more_info 88字段, 限 100 只避免太慢"""
    from tqcenter import tq
    tq.initialize(__file__)
    t0 = time.time()
    try:
        sectors = tq.get_sector_list() or []
        all_set = set()
        for s in sectors[:5]:  # 仅前 5 个板块
            cs = tq.get_stock_list_in_sector(s) or []
            all_set.update(cs)
        codes = sorted(all_set)[:100]  # 限 100 只
        ok = 0
        for c in codes:
            tdx = c if c.endswith(('.SH', '.SZ')) else (
                f'{c}.SH' if c.startswith('6') else
                (c if c.startswith(('88', '9')) else f'{c}.SZ')
            )
            try:
                d = tq.get_more_info(tdx, field_list=[])
                if d: ok += 1
            except: pass
        elapsed = time.time() - t0
        q.put((label, 'OK', ok, elapsed))
    except Exception as e:
        elapsed = time.time() - t0
        q.put((label, f'ERR: {e}', 0, elapsed))
    finally:
        try: tq.close()
        except: pass


def main():
    q = Queue()
    t_start = time.time()

    procs = [
        Process(target=worker_get_pricevol, args=(q, 'pricevol')),
        Process(target=worker_get_market_data, args=(q, 'market_data')),
        Process(target=worker_get_more_info, args=(q, 'more_info')),
    ]
    print(f'[{datetime.now().strftime("%H:%M:%S")}] 启动 3 子进程...')
    for p in procs:
        p.start()
        print(f'  PID {p.pid}: {p.name}')

    print(f'[{datetime.now().strftime("%H:%M:%S")}] 等待所有子进程完成...')
    for p in procs:
        p.join(timeout=180)  # 3 分钟超时
        if p.is_alive():
            print(f'  PID {p.pid} 超时, 强制结束')
            p.terminate()
            p.join()

    total = time.time() - t_start
    print(f'\n[{datetime.now().strftime("%H:%M:%S")}] 全部完成, 总耗时 {total:.2f}s')
    print('--- 3 接口结果 ---')
    results = []
    while not q.empty():
        r = q.get()
        results.append(r)
        print(f'  {r[0]:<15s} {r[1]:<10s} count={r[2]:<5d} 耗时 {r[3]:.2f}s')
    print(f'\n并行总耗时: {total:.2f}s')
    if results:
        # 串行估算
        serial_est = sum(r[3] for r in results)
        print(f'串行估算:   {serial_est:.2f}s (加速比 {serial_est/total:.2f}x)')


if __name__ == '__main__':
    main()
