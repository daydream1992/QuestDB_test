"""live_5min: 真实 5 分钟连续采集 + K线/指标/信号/飞书 全流程

数据源:  tqcenter.get_market_snapshot  (限 100 只, 真实 tick)
持续:    5 分钟 (默认, 可改 --dur)
并发:    writer(每 3s 取一次) / synth(每 30s) / indic(每 60s) / signal(每 30s)
汇报:    飞书推送"开始" + 每分钟心跳 + 信号 + "结束汇总"

snapshot_time = 系统当前时间 (盘后取价也用 now 作时戳, 让 SAMPLE BY 有 5m 桶)
"""
import os, sys, time, threading, queue, argparse, random
from pathlib import Path
from datetime import datetime, timedelta
import psycopg2
import requests
import pandas as pd
from dotenv import load_dotenv
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

# 1) 加载 tqcenter (真实数据源)
sys.path.insert(0, r'K:\txdlianghua\PYPlugins\sys')
from tqcenter import tq

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
logger.add(LOG_DIR / 'live5min_{time:YYYYMMDD_HHmmss}.log')

# 100 只代码 (get_market_snapshot 限制)
CODES = [
    '000001.SZ','000002.SZ','000063.SZ','000333.SZ','000651.SZ','000858.SZ','000725.SZ',
    '600000.SH','600036.SH','600519.SH','600276.SH','600887.SH','600030.SH','601318.SH',
    '601398.SH','601988.SH','600028.SH','600050.SH','600196.SH','600585.SH',
    '300750.SZ','300059.SZ','300015.SZ','300124.SZ','300760.SZ','300142.SZ',
    '002415.SZ','002475.SZ','002594.SZ','002230.SZ','002714.SZ','002466.SZ',
    '688981.SH','688041.SH','688012.SH','688271.SH','688111.SH','688599.SH',
    '601012.SH','603259.SH','603501.SH','688981.SH','600900.SH','601628.SH',
    '600905.SH','601888.SH','600436.SH','600763.SH','603392.SH','600276.SH',
    '000876.SZ','000895.SZ','000568.SZ','000338.SZ','000625.SZ','000792.SZ',
    '600104.SH','600690.SH','600703.SH','600745.SH','600848.SH','600886.SH',
    '000069.SZ','000157.SZ','000402.SZ','000538.SZ','000625.SZ','000768.SZ',
    '000776.SZ','000938.SZ','002230.SZ','002241.SZ','002371.SZ','002415.SZ',
    '002466.SZ','002475.SZ','002555.SZ','002607.SZ','002648.SZ','002714.SZ',
    '002841.SZ','002916.SZ','300003.SZ','300014.SZ','300015.SZ','300033.SZ',
    '300059.SZ','300122.SZ','300124.SZ','300142.SZ','300347.SZ','300408.SZ',
    '300413.SZ','300433.SZ','300498.SZ','300601.SZ','300628.SZ','300661.SZ',
    '300674.SZ','300699.SZ','300750.SZ','300760.SZ','300782.SZ','300866.SZ',
    '300999.SZ','301236.SZ','301269.SZ','688012.SH','688041.SH','688111.SH',
    '688271.SH','688599.SH',
]
CODES = list(dict.fromkeys(CODES))[:100]  # 去重 + 限 100
N_CODES = len(CODES)


# ---------- 全局状态 ----------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.writer_n = 0
        self.writer_err = 0
        self.synth_runs = 0
        self.synth_1m_n = 0
        self.synth_5m_n = 0
        self.indic_runs = 0
        self.indic_n = 0
        self.signal_runs = 0
        self.signal_n = 0
        self.signal_pushed = 0
        self.heartbeat = 0
S = State()


def connect():
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def push(text):
    """飞书推送 (异步不阻塞主流程)"""
    try:
        requests.post(WEBHOOK, json={'msg_type': 'text', 'content': {'text': text}}, timeout=10)
    except Exception as e:
        logger.warning(f'飞书推送失败: {e}')


# ---------- writer 真实采集 ----------
class Writer(threading.Thread):
    def __init__(self, dur):
        super().__init__(daemon=True)
        self.dur = dur
        self.stop_evt = threading.Event()
        self.latencies = []

    def run(self):
        # tqcenter 初始化
        tq.initialize(__file__)
        con = connect()
        cur = con.cursor()
        rnd = random.Random()
        sql = ("INSERT INTO qd_snapshots_realtime "
               "(code, snapshot_time, now, open, high, low, last_close, volume, amount, buyp1, sellp1) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        end = time.time() + self.dur
        while not self.stop_evt.is_set() and time.time() < end:
            t_loop = time.time()
            n = 0
            n_err = 0
            for code in CODES:
                try:
                    d = tq.get_market_snapshot(stock_code=code, field_list=[])
                    if not d:
                        continue
                    now = float(d.get('Now', 0) or 0)
                    op  = float(d.get('Open', 0) or 0)
                    hi  = float(d.get('Max', 0) or 0)
                    lo  = float(d.get('Min', 0) or 0)
                    lc  = float(d.get('LastClose', 0) or 0)
                    vol = int(d.get('Volume', 0) or 0)
                    amt = float(d.get('Amount', 0) or 0)
                    if now <= 0:
                        continue
                    t_now = datetime.now()
                    cur.execute(sql, (code, t_now, now, op, hi, lo, lc, vol, amt, now + 0.01, now - 0.01))
                    n += 1
                except Exception as e:
                    n_err += 1
                    if n_err <= 3:
                        logger.warning(f'  {code} 取价失败: {str(e)[:80]}')
            with S.lock:
                S.writer_n += n
                S.writer_err += n_err
            self.latencies.append((time.time() - t_loop) * 1000)
            # 控制节奏: 总周期 3s
            sleep = 3.0 - (time.time() - t_loop)
            if sleep > 0:
                time.sleep(sleep)
        con.close()
        tq.close()

    def stop(self):
        self.stop_evt.set()


# ---------- synth / indic / signal 调度 ----------
def run_synth(con):
    """qd_02 SAMPLE BY 合成 1m/5m"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('qd_02', str(Path(__file__).resolve().parent / '2_kline' / 'qd_02_synth_kline.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    t0 = time.time()
    try:
        for dst, minutes in [('qd_kline_1m', '1m'), ('qd_kline_5m', '5m')]:
            n = mod.synth_one(con, 'qd_snapshots_realtime', dst, minutes)
            if dst == 'qd_kline_1m': S.synth_1m_n = n
            if dst == 'qd_kline_5m': S.synth_5m_n = n
        S.synth_runs += 1
    except Exception as e:
        logger.error(f'synth 错误: {e}')


def run_indic(con):
    """qd_03 计算指标 (默认 5m + 12/26/9)"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('qd_03', str(Path(__file__).resolve().parent / '3_indicators' / 'qd_03_indicators.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    try:
        df = mod.fetch_kline(con)
        if df.empty:
            return
        all_rows = []
        for code, g in df.groupby('code'):
            rs = mod.calc_one_code(g)
            all_rows.extend(rs)
        n = mod.save(con, all_rows)
        S.indic_n = n
        S.indic_runs += 1
    except Exception as e:
        logger.error(f'indic 错误: {e}')


def run_indic_quick(con):
    """1m K线 + 短周期 MACD(5,10,5) + BOLL(10,1.5) + 压支(10) — 5 分钟能出信号"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('qd_03q', str(Path(__file__).resolve().parent / '3_indicators' / 'qd_03_indicators.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.MACD_FAST, mod.MACD_SLOW, mod.MACD_SIGNAL = 5, 10, 5
    mod.BOLL_N, mod.BOLL_K = 10, 1.5
    mod.PRESS_N = 10
    mod.SRC_KLINE = 'qd_kline_1m'
    mod.DST = 'qd_indicators'
    try:
        df = mod.fetch_kline(con)
        if df.empty:
            return
        all_rows = []
        for code, g in df.groupby('code'):
            rs = mod.calc_one_code(g)
            all_rows.extend(rs)
        n = mod.save(con, all_rows)
        S.indic_n = n
        S.indic_runs += 1
        logger.info(f'  indic_quick: 1m K线 + 短周期, 入库 {n} 条')
    except Exception as e:
        logger.error(f'indic_quick 错误: {e}')


def run_signal(con):
    """qd_04 扫信号 + 推飞书"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('qd_04', str(Path(__file__).resolve().parent / '4_signals' / 'qd_04_signal_lark.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    try:
        df = mod.fetch_indicators(con)
        if df.empty:
            return
        signals = mod.detect_signals(df)
        now = datetime.now()
        for sig in signals:
            if not mod.can_push(con, sig['code'], sig['signal_type'], now):
                mod.save_signal(con, sig, pushed=False)
                continue
            ok = mod.push_lark(sig)
            mod.save_signal(con, sig, pushed=ok)
            if ok:
                mod.mark_pushed(con, sig['code'], sig['signal_type'], now)
                with S.lock:
                    S.signal_pushed += 1
        with S.lock:
            S.signal_n += len(signals)
            S.signal_runs += 1
    except Exception as e:
        logger.error(f'signal 错误: {e}')


# ---------- 调度器 ----------
def scheduler(con, dur, stop_evt, quick=False):
    """定时触发 synth/indic/signal, 同时心跳推送飞书

    quick=True:  用 1m K线 + 短周期 MACD, 5 分钟能出信号
    quick=False: 用 5m K线 + 12/26/9 MACD, 需盘中 2.5h+ 数据才出信号
    """
    indic_fn = run_indic_quick if quick else run_indic
    last_synth = last_indic = last_signal = last_heart = 0
    while not stop_evt.is_set():
        now = time.time()
        if now - last_synth >= 30:
            run_synth(con); last_synth = now
        if now - last_indic >= 60:
            indic_fn(con); last_indic = now
        if now - last_signal >= 30:
            run_signal(con); last_signal = now
        if now - last_heart >= 60:
            with S.lock:
                S.heartbeat += 1
            push(f'[心跳 {S.heartbeat}] {datetime.now().strftime("%H:%M:%S")} '
                 f'写 {S.writer_n}  5mK {S.synth_5m_n}  指 {S.indic_n}  信 {S.signal_n}(推{S.signal_pushed})')
            last_heart = now
        time.sleep(1)


def main(dur, quick=False):
    mode = 'quick(1m+短MACD)' if quick else '标准(5m+12/26/9)'
    # 推送开始
    push(f'[live5min] 开始  持续 {dur}s  监控 {N_CODES} 只  模式 {mode}')

    # 清空
    con = connect()
    cur = con.cursor()
    for tbl in ['qd_snapshots_realtime', 'qd_kline_1m', 'qd_kline_5m',
                'qd_indicators', 'qd_signals', 'qd_signal_log']:
        try: cur.execute(f'TRUNCATE TABLE {tbl}')
        except Exception: pass

    # 启动 writer + scheduler
    stop_evt = threading.Event()
    writer = Writer(dur); writer.start()
    sch_t = threading.Thread(target=scheduler, args=(con, dur, stop_evt, quick), daemon=True)
    sch_t.start()

    t0 = time.time()
    while time.time() - t0 < dur:
        time.sleep(5)
        e = time.time() - t0
        with S.lock:
            print(f'  [{e:5.1f}s] 写 {S.writer_n:5d} 行/错{S.writer_err:2d}  '
                  f'5mK {S.synth_5m_n:3d}  指 {S.indic_n:3d}  '
                  f'信 {S.signal_n:2d}/推{S.signal_pushed:2d}', flush=True)

    # 收尾
    stop_evt.set()
    writer.stop()
    writer.join(timeout=10)
    sch_t.join(timeout=5)
    elapsed = time.time() - t0

    # 最终汇总 (新连接避免 SELECT snapshot)
    con.close()
    import time as _t; _t.sleep(0.3)
    con2 = connect()
    cur = con2.cursor()
    cur.execute("SELECT count(*), min(snapshot_time), max(snapshot_time) FROM qd_snapshots_realtime")
    sw, ts_min, ts_max = cur.fetchone()
    cur.execute("SELECT count(*) FROM qd_kline_5m"); k5 = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM qd_indicators"); ki = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM qd_signals"); ss = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM qd_signals WHERE pushed=true"); sp = cur.fetchone()[0]
    con2.close()

    w_rate = sw / elapsed if elapsed else 0
    print()
    print('=' * 60)
    print(f'live5min 完成  实际 {elapsed:.1f}s  监控 {N_CODES} 只')
    print(f'  写入: {sw} 行 ({w_rate:.0f} rows/s)  错误 {S.writer_err}')
    print(f'  时间范围: {ts_min} ~ {ts_max}')
    print(f'  5m K线: {k5}  指标: {ki}  信号: {ss} (推成功 {sp})')
    print(f'  飞书心跳: {S.heartbeat} 次')
    print('=' * 60)

    push(f'[live5min] 完成  {w_rate:.0f} rows/s  5mK {k5}  指 {ki}  '
         f'信 {ss}(推{sp})  心跳 {S.heartbeat}  模式 {mode}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dur', type=int, default=300, help='持续秒数, 默认 300 (5 分钟)')
    ap.add_argument('--quick', action='store_true', help='短周期 MACD 模式 (1m K线, 5 分钟内出信号)')
    args = ap.parse_args()
    main(dur=args.dur, quick=args.quick)
