"""e2e_mock: 端到端演示 (盘后用 mock 数据)

流程:
  1. 准备 5 只 mock 实时数据 → qd_snapshots_realtime
  2. qd_02 SAMPLE BY 合成 1m/5m K 线
  3. qd_03 计算 MACD + 压力位 + 布林带
  4. qd_04 扫信号 → 推飞书

设计:
  - 5 只股票, 各 120 分钟 tick (9:30 ~ 11:30)
  - 价格走势: 1 涨 1 跌 3 震荡/拐点 (确保触发金叉/死叉/突破/跌破)
"""
import os
import random
from pathlib import Path
from datetime import datetime, timedelta
import psycopg2
import requests
from dotenv import load_dotenv
from loguru import logger

# 让子脚本可 import 公共
import sys
sys.path.insert(0, r'K:\QuestDB_test')

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
logger.add(LOG_DIR / 'e2e_mock_{time:YYYYMMDD_HHmmss}.log')

CODES = ['000001.SZ', '600000.SH', '600519.SH', '000002.SZ', '300750.SZ']
BASE_TIME = datetime(2026, 7, 3, 9, 30, 0)
N_TICKS = 240  # 240 分钟 (4 小时), 5m K 线 = 48 根, 扣 26 周期 MACD + 20 周期 BOLL 仍有足够交叉窗口

# 每只股票 120 个 close 价格 (确定性 + 随机扰动)
def gen_closes(code):
    rnd = random.Random(hash(code) & 0xffff)
    if code == '000001.SZ':
        # 强势上涨: 10 → 12.5 (4小时涨 25%) → 应触发金叉 + 突破压力
        return [10.00 + 0.0104 * i + rnd.uniform(-0.015, 0.015) for i in range(N_TICKS)]
    if code == '600000.SH':
        # 单边下跌: 10 → 8.4 (跌 16%) → 应触发死叉 + 跌破支撑
        return [10.00 - 0.0067 * i + rnd.uniform(-0.015, 0.015) for i in range(N_TICKS)]
    if code == '600519.SH':
        # 先涨后跌: 10 → 10.8 (i<120) → 10.8 → 9.5 → 多重金叉/死叉
        return [10.00 + (0.0067 * i if i < 120 else 0.8 - 0.0108 * (i - 120)) + rnd.uniform(-0.02, 0.02) for i in range(N_TICKS)]
    if code == '000002.SZ':
        # 慢牛: 10 → 11.0 → 单次金叉
        return [10.00 + 0.0042 * i + rnd.uniform(-0.01, 0.01) for i in range(N_TICKS)]
    # 300750.SZ: 涨后急跌 → 金叉 + 死叉
    return [10.00 + (0.04 * i if i < 80 else 3.2 - 0.022 * (i - 80)) + rnd.uniform(-0.01, 0.01) for i in range(N_TICKS)]


def seed_realtime(con):
    """清空 + 灌 mock 实时数据"""
    cur = con.cursor()
    # QuestDB 不支持无 WHERE 的 DELETE, 用 TRUNCATE
    for tbl in ['qd_snapshots_realtime', 'qd_kline_1m', 'qd_kline_5m',
                'qd_indicators', 'qd_signals', 'qd_signal_log']:
        try:
            cur.execute(f"TRUNCATE TABLE {tbl}")
        except Exception as e:
            logger.warning(f'TRUNCATE {tbl} 失败: {e}')
            con.rollback()
    con.commit()
    logger.info('已清空 6 张表')

    rows = []
    for code in CODES:
        closes = gen_closes(code)
        rnd = random.Random(hash(code) & 0xffff)
        for i, c in enumerate(closes):
            t = BASE_TIME + timedelta(minutes=i)
            o = c + rnd.uniform(-0.02, 0.02)
            h = max(c, o) + rnd.uniform(0, 0.03)
            l = min(c, o) - rnd.uniform(0, 0.03)
            last_close = closes[i - 1] if i > 0 else c
            amt = abs(c - last_close) * 1_000_000 + rnd.uniform(10_000, 50_000)
            vol = int(amt / max(c, 0.01))
            rows.append((code, t, c, o, h, l, last_close, vol, amt, o + 0.01, o - 0.01))

    cur.executemany("""
        INSERT INTO qd_snapshots_realtime
        (code, snapshot_time, now, open, high, low, last_close, volume, amount, buyp1, sellp1)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    con.commit()
    logger.info(f'已灌入 {len(rows)} 条 mock 实时数据 (5 stocks × 120 ticks)')


def run_step(name, fn):
    logger.info(f'=== {name} ===')
    try:
        fn()
        logger.info(f'  ✔ {name} OK')
    except Exception as e:
        logger.error(f'  ✘ {name} 失败: {e}')
        raise


def main():
    logger.info('🚀 e2e mock 全流程开始')
    # 0. 推送"开始"通知
    requests.post(WEBHOOK, json={
        "msg_type": "text",
        "content": {"text": f"[e2e mock] 开始 {datetime.now().strftime('%H:%M:%S')}"}
    }, timeout=10)

    con = connect()
    try:
        # 1. seed
        run_step('seed mock 实时数据', lambda: seed_realtime(con))

        # 2-4. 跑 qd_02/03/04 (共享 e2e 的 con 避免 QuestDB 9.4.3 跨连接 read-after-write 延迟)
        run_step('qd_02 SAMPLE BY 合成 K 线', lambda: _run_module('qd_02_synth_kline', base=Path(r'K:\QuestDB_test\2_kline'), con=con))
        run_step('qd_03 计算指标',         lambda: _run_module('qd_03_indicators', base=Path(r'K:\QuestDB_test\3_indicators'), con=con))
        run_step('qd_04 信号 + 飞书',       lambda: _run_module('qd_04_signal_lark', base=Path(r'K:\QuestDB_test\4_signals'), con=con))

        # 5. 汇总 (用新连接绕开 QuestDB 9.4.3 SELECT snapshot 缓存)
        con.close()
        import time as _t; _t.sleep(0.3)  # 留点时间让 QuestDB 把 WAL 落盘
        con3 = connect()
        cur = con3.cursor()
        cur.execute("SELECT count(*) FROM qd_kline_1m")
        n1 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_kline_5m")
        n5 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_indicators")
        ni = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_signals")
        ns = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_signals WHERE pushed = true")
        ns_ok = cur.fetchone()[0]
        con3.close()
        logger.info(f'汇总: 1mK={n1}  5mK={n5}  指标={ni}  信号={ns} (推送成功 {ns_ok})')

        requests.post(WEBHOOK, json={
            "msg_type": "text",
            "content": {
                "text": (
                    f"[e2e mock] 完成\n"
                    f"1m K线: {n1}  5m K线: {n5}\n"
                    f"指标: {ni}  信号: {ns}  推送成功: {ns_ok}"
                )
            }
        }, timeout=10)
    finally:
        try:
            con.close()
        except Exception:
            pass
    logger.info('🏁 e2e mock 全部完成')


def connect():
    """QuestDB 9.4.3 PG 协议存在事务快照延迟, 用 autocommit=True 避免 read-after-write 问题"""
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def _run_module(modname, base: Path, con=None):
    """动态 import + 调 run(con=con)"""
    import importlib
    spec = importlib.util.spec_from_file_location(modname, str(base / f'{modname}.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if con is not None:
        mod.run(con=con)
    else:
        mod.run()


if __name__ == '__main__':
    main()
