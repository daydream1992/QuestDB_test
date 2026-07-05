"""_stress_rw: 读写并发压测 (QuestDB 9.4.3 PG 协议)

模拟:
  - 1 个 writer: 持续往 qd_snapshots_realtime 写 tick (5000 只 × N 轮)
  - 多个 reader: 并发 SAMPLE BY 合成 5m K 线 + 读指标 + 信号检测

测量:
  - 写入吞吐 (rows/s)
  - 读取延迟 (ms)
  - 错误率
  - 写读并发时 QuestDB 是否稳定

用法:
  python _stress_rw.py              # 默认: 2 readers × 30s
  python _stress_rw.py --dur 60 --readers 4
"""
import os, sys, time, random, argparse, threading, queue
from pathlib import Path
from datetime import datetime, timedelta
import psycopg2
import requests
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent / '.env')

QDB = dict(
    host=os.environ['QDB_HOST'],
    port=int(os.environ['QDB_PORT']),
    user=os.environ['QDB_USER'],
    password=os.environ['QDB_PASSWORD'],
    dbname=os.environ['QDB_DBNAME'],
)
WEBHOOK = os.environ['LARK_WEBHOOK_URL']

LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / 'stress_{time:YYYYMMDD_HHmmss}.log')

# 测试用 50 只代码 (覆盖度比 e2e 大, 比 5000 全市场小, 跑得动)
CODES = [f'{600000+i:06d}.SH' for i in range(50)] + [f'{1+i:06d}.SZ' for i in range(50)]
N_CODES = len(CODES)


def connect():
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


# ---------- writer ----------
class Writer(threading.Thread):
    def __init__(self, dur_sec, batch_size=200):
        super().__init__(daemon=True)
        self.dur = dur_sec
        self.batch = batch_size
        self.written = 0
        self.errors = 0
        self.stop_evt = threading.Event()
        self.latencies = []

    def run(self):
        con = connect()
        cur = con.cursor()
        rnd = random.Random(42)
        base = datetime.now()
        tick = 0
        sql = ("INSERT INTO qd_snapshots_realtime "
               "(code, snapshot_time, now, open, high, low, last_close, volume, amount, buyp1, sellp1) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        end_time = time.time() + self.dur
        while not self.stop_evt.is_set() and time.time() < end_time:
            rows = []
            for _ in range(self.batch):
                code = rnd.choice(CODES)
                t = base + timedelta(milliseconds=tick * 100)
                px = 10 + rnd.uniform(-0.5, 0.5)
                rows.append((code, t, px, px, px, px, px, 1000, 10000, px + 0.01, px - 0.01))
                tick += 1
            t0 = time.time()
            try:
                cur.executemany(sql, rows)
                self.written += len(rows)
                self.latencies.append((time.time() - t0) * 1000)
            except Exception as e:
                self.errors += 1
                logger.error(f'writer 错误: {e}')
        con.close()

    def stop(self):
        self.stop_evt.set()


# ---------- reader ----------
class Reader(threading.Thread):
    """并发做: SAMPLE BY 5m + 读指标"""
    def __init__(self, dur_sec, name):
        super().__init__(daemon=True)
        self.dur = dur_sec
        self.name = name
        self.reads = 0
        self.errors = 0
        self.stop_evt = threading.Event()
        self.latencies = []
        self.kline_counts = []
        self.ind_counts = []

    def run(self):
        con = connect()
        cur = con.cursor()
        end_time = time.time() + self.dur
        rnd = random.Random(hash(self.name) & 0xffff)
        while not self.stop_evt.is_set() and time.time() < end_time:
            t0 = time.time()
            try:
                # 1. 读快照
                cur.execute("SELECT count(*) FROM qd_snapshots_realtime")
                n_snap = cur.fetchone()[0]

                # 2. 跑一次 5m SAMPLE BY 模拟
                cur.execute("""
                    SELECT count(*) FROM (
                        SELECT code, snapshot_time as kline_time,
                               first(open) as o, max(high) as h, min(low) as l,
                               last(now) as c, sum(amount) as a
                        FROM qd_snapshots_realtime
                        SAMPLE BY 5m
                    )
                """)
                n_kline = cur.fetchone()[0]

                # 3. 读指标
                cur.execute("SELECT count(*) FROM qd_indicators")
                n_ind = cur.fetchone()[0]

                self.reads += 1
                self.latencies.append((time.time() - t0) * 1000)
                self.kline_counts.append(n_kline)
                self.ind_counts.append(n_ind)
            except Exception as e:
                self.errors += 1
                logger.error(f'reader {self.name} 错误: {e}')
            time.sleep(rnd.uniform(0.05, 0.2))
        con.close()

    def stop(self):
        self.stop_evt.set()


def stats(name, vals):
    if not vals:
        return f'{name}=空'
    s = sorted(vals)
    n = len(s)
    return (f'{name}: n={n}  avg={sum(s)/n:.1f}  '
            f'p50={s[n//2]:.1f}  p95={s[int(n*0.95)]:.1f}  p99={s[int(n*0.99)]:.1f}  max={s[-1]:.1f} (ms)')


def run(dur, n_readers):
    logger.info(f'▶ 压测开始 dur={dur}s readers={n_readers} codes={N_CODES}')

    # 推送开始
    try:
        requests.post(WEBHOOK, json={'msg_type': 'text', 'content': {'text': f'[压测] 开始 {datetime.now().strftime("%H:%M:%S")} dur={dur}s readers={n_readers}'}}, timeout=10)
    except Exception:
        pass

    # 清空
    con = connect()
    cur = con.cursor()
    for tbl in ['qd_snapshots_realtime', 'qd_kline_1m', 'qd_kline_5m', 'qd_indicators', 'qd_signals', 'qd_signal_log']:
        try:
            cur.execute(f'TRUNCATE TABLE {tbl}')
        except Exception:
            pass
    con.close()
    logger.info('已清空 6 张表')

    # 启动
    writers = [Writer(dur_sec=dur) for _ in range(1)]
    readers = [Reader(dur_sec=dur, name=f'R{i}') for i in range(n_readers)]
    t0 = time.time()
    for w in writers: w.start()
    for r in readers: r.start()

    # 进度条
    while time.time() - t0 < dur + 2:
        time.sleep(2)
        elapsed = time.time() - t0
        w_done = sum(w.written for w in writers)
        print(f'  [{elapsed:5.1f}s] 写入 {w_done} 行  读 {sum(r.reads for r in readers)} 次', flush=True)
        if all(not w.is_alive() for w in writers) and all(not r.is_alive() for r in readers):
            break

    for w in writers: w.stop()
    for r in readers: r.stop()
    for w in writers: w.join(timeout=5)
    for r in readers: r.join(timeout=5)
    elapsed = time.time() - t0
    logger.info(f'压测结束, 实际耗时 {elapsed:.1f}s')

    # 汇总
    total_w = sum(w.written for w in writers)
    total_we = sum(w.errors for w in writers)
    total_r = sum(r.reads for r in readers)
    total_re = sum(r.errors for r in readers)
    w_lat = []
    for w in writers: w_lat.extend(w.latencies)
    r_lat = []
    for r in readers: r_lat.extend(r.latencies)
    last_k = readers[0].kline_counts[-1] if readers and readers[0].kline_counts else 0
    last_i = readers[0].ind_counts[-1] if readers and readers[0].ind_counts else 0

    w_rate = total_w / elapsed if elapsed else 0
    r_rate = total_r / elapsed if elapsed else 0
    print()
    print('=' * 60)
    print(f'  写入: {total_w} 行 / {elapsed:.1f}s = {w_rate:.0f} rows/s  错误 {total_we}')
    print(f'  读取: {total_r} 次 / {elapsed:.1f}s = {r_rate:.1f} qps   错误 {total_re}')
    print(f'  最后一次读到: 5m K线数={last_k}  指标数={last_i}')
    print(f'  写入延迟: {stats("w_lat", w_lat)}')
    print(f'  读取延迟: {stats("r_lat", r_lat)}')
    print('=' * 60)

    # 推送结果
    try:
        requests.post(WEBHOOK, json={'msg_type': 'text', 'content': {'text':
            f'[压测] 完成\n'
            f'写: {w_rate:.0f} rows/s  ({total_w} 行, 错 {total_we})\n'
            f'读: {r_rate:.1f} qps  ({total_r} 次, 错 {total_re})\n'
            f'读延迟 p50={sorted(r_lat)[len(r_lat)//2] if r_lat else 0:.1f}ms  '
            f'p95={sorted(r_lat)[int(len(r_lat)*0.95)] if r_lat else 0:.1f}ms'
        }}, timeout=10)
    except Exception:
        pass


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dur', type=int, default=20)
    ap.add_argument('--readers', type=int, default=2)
    args = ap.parse_args()
    run(dur=args.dur, n_readers=args.readers)
