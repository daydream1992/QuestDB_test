"""subscribe_hq 共存探针 (方案A: 回调只 queue, 主循环串行 tq)

验证目标 (盘中 9:30-9:45 跑, python testv10.2/_probe_subscribe.py):
  1. subscribe_hq 回调是否及时触发 (频率/延迟/丢失)
  2. 主循环 get_market_snapshot 在盘中是否正常返回 Open/Now
  3. send_warn 是否成功 (客户端收到预警)
  4. 与 pricevol 轮询共存: 回调处理和轮询是否互相阻塞 + 队列积压

架构 (方案A, 根除 COM 并发未知):
  回调线程: on_data → SIGNAL_Q.put(code)   ← 唯一动作, 不碰 tq
  主线程:    SIGNAL_Q.get → get_market_snapshot → 涨幅>5% → unsubscribe_hq + send_warn
             每 30s 一次 scan_universe(pricevol) 模拟 radar 轮询
  → 所有 tq 调用串行在主线程, 和官方示例调用链一致 (snapshot→unsubscribe→send_warn)

观察点:
  - 回调时间戳密集度 (开盘秒级应密集)
  - 队列积压 SIGNAL_Q.qsize() (积压大=主循环处理跟不上)
  - snapshot 返回 Now/Open 合理 (非0非空)
  - send_warn 返回 ErrorId=0
  - 轮询和回调处理不互相卡死
"""

import bootstrap
bootstrap.ensure_paths()

import json  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from queue import Queue, Empty  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from tqcenter import tq  # noqa: E402
import ticker as tk  # noqa: E402

# 盘中改: 5-10 只活跃股/昨涨停候选 (探针验机制, 用大盘活跃股必有行情更新)
SUB_CODES = [
    '600519.SH',  # 贵州茅台
    '300750.SZ',  # 宁德时代
    '300059.SZ',  # 东方财富
    '688981.SH',  # 中芯国际
    '000725.SZ',  # 京东方A
    '601318.SH',  # 中国平安
    '002594.SZ',  # 比亚迪
    '600036.SH',  # 招商银行
]
SIGNAL_Q: Queue = Queue()
OPEN_RISE_THRESHOLD = 5.0   # 相对开盘价涨幅阈值
STOP = False


def on_data(data_str: str) -> None:
    """回调: 只把 code 塞队列 (不碰任何 tq 接口, 方案A 核心)。"""
    try:
        code = json.loads(data_str).get('Code')
    except Exception:  # noqa: BLE001
        return
    SIGNAL_Q.put(code)
    print(f'[回调 {datetime.now().strftime("%H:%M:%S.%f")[:-3]}] {code}')


def sim_producer(codes: list[str], interval: float) -> None:
    """模拟 subscribe_hq 回调: 定时往队列 put code (不碰 tq, 和真回调行为一致)。"""
    while not STOP:
        SIGNAL_Q.put(random.choice(codes))
        time.sleep(interval)


def main(simulate: bool = False):
    global STOP
    # 模拟模式: 不 subscribe, producer 线程造信号; 阈值放低强制触发 send_warn 验发送; 轮询加快
    threshold = -100.0 if simulate else OPEN_RISE_THRESHOLD
    poll_interval = 5 if simulate else 30
    init()
    try:
        if simulate:
            th = threading.Thread(target=sim_producer, args=(SUB_CODES, 0.3), daemon=True)
            th.start()
            print(f'[模拟] producer 每0.3s put code (模拟回调), 主循环串行消费+轮询{poll_interval}s')
            print('       验证: snapshot返回 / send_warn发送 / 队列积压 / 轮询共存不阻塞\n')
        else:
            r = safe_call(tq.subscribe_hq, stock_list=SUB_CODES, callback=on_data)
            print(f'已订阅 {len(SUB_CODES)} 只: {SUB_CODES}')
            print(f'subscribe 返回: {r}')
            print('进入主循环 (Ctrl+C 退出) — 观察 回调触发 / snapshot / send_warn / 轮询共存\n')
        t_poll = time.time()
        n_signal = n_fire = 0
        while True:
            # 1. 串行处理回调信号 (主线程调 tq)
            try:
                code = SIGNAL_Q.get(timeout=0.1)
                n_signal += 1
                snap = safe_call(tq.get_market_snapshot, stock_code=code,
                                 field_list=['Now', 'Open', 'LastClose']) or {}
                now_p = float(snap.get('Now') or 0)
                open_p = float(snap.get('Open') or 0)
                rise = ((now_p - open_p) / open_p * 100) if open_p > 0 else 0
                flag = '⚠️' if rise > OPEN_RISE_THRESHOLD else '  '
                print(f'{flag} [{code}] Now={now_p} Open={open_p} 相对开盘 {rise:+.2f}% '
                      f'(积压 {SIGNAL_Q.qsize()})')
                if rise > threshold:
                    n_fire += 1
                    if not simulate:
                        safe_call(tq.unsubscribe_hq, stock_list=[code])
                    wr = safe_call(tq.send_warn,
                                   stock_list=[code],
                                   time_list=[datetime.now().strftime('%Y%m%d%H%M%S')],
                                   price_list=[str(now_p)],
                                   close_list=[snap.get('LastClose', '0')],
                                   volum_list=['0'], bs_flag_list=['2'],
                                   warn_type_list=['0'],
                                   reason_list=[f'{"模拟" if simulate else "开盘拉升"}{rise:.1f}%'],
                                   count=1)
                    print(f'   → send_warn: {wr}')
            except Empty:
                pass
            # 2. 定时 pricevol 轮询 (模拟 radar, 验共存 + 阻塞)
            if time.time() - t_poll > poll_interval:
                t_poll = time.time()
                t0 = time.time()
                df = tk.scan_universe()
                dt = time.time() - t0
                print(f'  [轮询 {datetime.now().strftime("%H:%M:%S")}] pricevol {len(df)} 只 '
                      f'耗时 {dt:.1f}s | 累计信号 {n_signal} 触发 {n_fire} | 积压 {SIGNAL_Q.qsize()}')
    except KeyboardInterrupt:
        print('\n用户中断')
    finally:
        STOP = True
        if not simulate:
            try:
                safe_call(tq.unsubscribe_hq, stock_list=SUB_CODES)
            except Exception:  # noqa: BLE001
                pass
        close()
        print('已清理 + 关闭连接')


if __name__ == '__main__':
    main(simulate='--simulate' in sys.argv)
