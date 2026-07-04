"""e2e_real: 端到端 — 真实 tqcenter 数据 + QuestDB + 飞书

流程 (与 e2e_mock 同样的下游链路, 但数据源全用 tqcenter 真实 API):
  1. get_market_data(period='1m', count=240) 拉 5 只最近 4 小时 1m K 线  → qd_kline_1m
  2. get_pricevol(stock_list=全市场) 拉全场当前价量                       → qd_pricevol (对照)
  3. qd_03: 从 qd_kline_1m 算 MACD/BOLL/压支                            → qd_indicators
  4. qd_04: 扫 indicators → 触发信号 → 飞书 Webhook                      → qd_signals

目的: 验证真实数据 (非 mock) 走通整条管道
"""
import os
import sys
import time
import importlib.util
from pathlib import Path
from datetime import datetime
import psycopg2
import requests
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

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\sys')
from tqcenter import tq
tq.initialize(__file__)

LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / 'e2e_real_{time:YYYYMMDD_HHmmss}.log')

# 5 只 — 覆盖 主板/创业板/科创板/北交所
CODES = ['000001.SZ', '600000.SH', '600519.SH', '000002.SZ', '300750.SZ']


def connect():
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def to_tdx(code: str) -> str:
    if code.endswith('.SH') or code.endswith('.SZ'):
        return code
    return f'{code}.SH' if code.startswith('6') else f'{code}.SZ'


def step1_pull_kline(con):
    """1. get_market_data 拉 1m/5m K 线 (5 只 × 240/48 根) → qd_kline_1m + qd_kline_5m"""
    cur = con.cursor()
    # QuestDB 9.4.3 不支持 DELETE 但支持 TRUNCATE; 仍包 try 兜底
    for tbl in ['qd_kline_1m', 'qd_kline_5m', 'qd_indicators', 'qd_signals', 'qd_signal_log']:
        try:
            cur.execute(f"TRUNCATE TABLE {tbl}")
            logger.info(f'  清空 {tbl}')
        except Exception as e:
            logger.warning(f'  TRUNCATE {tbl} 失败: {e}')
    con.commit()
    logger.info('已清空 kline/indicators/signals 表')

    tdx_codes = [to_tdx(c) for c in CODES]
    n_1m, u_1m = _pull_one_period(con, '1m', 240, tdx_codes, 'qd_kline_1m')
    n_5m, u_5m = _pull_one_period(con, '5m', 48, tdx_codes, 'qd_kline_5m')
    return (n_1m, u_1m), (n_5m, u_5m)


def _pull_one_period(con, period: str, count: int, tdx_codes: list, tbl: str):
    """单周期: get_market_data → 写表"""
    t0 = time.time()
    d = tq.get_market_data(stock_list=tdx_codes, period=period, count=count)
    fetch_t = time.time() - t0
    logger.info(f'get_market_data period={period} count={count} 耗时 {fetch_t:.2f}s, 返回 {len(d)} 字段')

    if 'Low' not in d:
        raise RuntimeError(f'返回字段异常: {list(d.keys())}')

    low_df = d['Low']
    codes_in_df = list(low_df.columns)
    rows = []
    for code_in_df in codes_in_df:
        clean_code = code_in_df
        if clean_code.count('.') >= 2:
            parts = clean_code.split('.')
            clean_code = f'{parts[0]}.{parts[1]}'
        for ts, lo in low_df[code_in_df].items():
            try:
                lo_v = float(lo) if not (lo != lo) else None
                if lo_v is None:
                    continue
                op_v = float(d['Open'].at[ts, code_in_df]) if not d['Open'].at[ts, code_in_df] != d['Open'].at[ts, code_in_df] else 0
                hi_v = float(d['High'].at[ts, code_in_df]) if not d['High'].at[ts, code_in_df] != d['High'].at[ts, code_in_df] else 0
                cl_v = float(d['Close'].at[ts, code_in_df]) if 'Close' in d and not d['Close'].at[ts, code_in_df] != d['Close'].at[ts, code_in_df] else lo_v
                amt_v = float(d['Amount'].at[ts, code_in_df]) if 'Amount' in d and not d['Amount'].at[ts, code_in_df] != d['Amount'].at[ts, code_in_df] else 0
                if hasattr(ts, 'to_pydatetime'):
                    ts = ts.to_pydatetime()
                rows.append((clean_code, ts, op_v, hi_v, lo_v, cl_v, amt_v))
            except Exception as e:
                logger.debug(f'  解析 {code_in_df}@{ts} 失败: {e}')

    logger.info(f'  {period} 解析 {len(rows)} 行')

    cur = con.cursor()
    cur.executemany(f"""
        INSERT INTO {tbl} (code, kline_time, open, high, low, close, sum_amount)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    con.commit()
    cur.execute(f"SELECT count(*), count(DISTINCT code) FROM {tbl}")
    n, u = cur.fetchone()
    logger.info(f'  {tbl} 写入: {n} 行, {u} 唯一 code')
    return n, u


def step2_pull_pricevol(con):
    """2. get_pricevol 拉全市场当前价量 → qd_pricevol (对照)"""
    cur = con.cursor()
    sectors = tq.get_sector_list() or []
    all_set = set()
    for s in sectors:
        cs = tq.get_stock_list_in_sector(s) or []
        all_set.update(cs)
    all_set.update(sectors)
    codes = [to_tdx(c) for c in sorted(all_set)]
    logger.info(f'全市场: {len(codes)} 只')

    t0 = time.time()
    d = tq.get_pricevol(stock_list=codes)
    fetch_t = time.time() - t0
    logger.info(f'get_pricevol 1 次 耗时 {fetch_t:.2f}s, 返回 {len(d)} 只')

    ts = datetime.now()
    rows = []
    for code, v in d.items():
        try:
            rows.append((
                code, ts,
                float(v.get('LastClose', 0) or 0),
                float(v.get('Now', 0) or 0),
                int(float(v.get('Volume', 0) or 0)),
            ))
        except: pass

    # 写 qd_pricevol (QuestDB 不支持 DELETE, DEDUP UPSERT KEYS(snapshot_time, code) 自动覆盖)
    cur.executemany("""
        INSERT INTO qd_pricevol (code, snapshot_time, last_close, now, volume)
        VALUES (%s,%s,%s,%s,%s)
    """, rows)
    con.commit()
    logger.info(f'qd_pricevol 写入: {len(rows)} 行, ts={ts}')


def step_run_module(modname, base):
    """动态加载 qd_03 / qd_04, 不传 con, 让模块自己管理连接 (避免共享连接状态)"""
    spec = importlib.util.spec_from_file_location(modname, str(base / f'{modname}.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run()  # con=None → 模块自己 connect/close


def main():
    requests.post(WEBHOOK, json={
        "msg_type": "text",
        "content": {"text": f"[e2e real] 开始 {datetime.now().strftime('%H:%M:%S')}"}
    }, timeout=10)

    con = connect()
    try:
        # 1. 拉真实 1m K 线
        logger.info('=== step 1: get_market_data 拉 1m K 线 ===')
        n1, u1 = step1_pull_kline(con)

        # 2. 拉全市场价量 (对照)
        logger.info('=== step 2: get_pricevol 拉全市场 ===')
        step2_pull_pricevol(con)

        # 3. 算指标
        logger.info('=== step 3: qd_03 算指标 ===')
        step_run_module('qd_03_indicators', Path(r'K:\QuestDB_test\3_indicators'))

        # 4. 信号 → 飞书
        logger.info('=== step 4: qd_04 信号 + 飞书 ===')
        step_run_module('qd_04_signal_lark', Path(r'K:\QuestDB_test\4_signals'))

        # 5. 汇总 (用新连接绕开 QuestDB 9.4.3 快照延迟)
        con.close()
        time.sleep(0.5)
        con2 = connect()
        cur = con2.cursor()
        cur.execute("SELECT count(*), count(DISTINCT code) FROM qd_kline_1m")
        nk1, uk1 = cur.fetchone()
        cur.execute("SELECT count(*) FROM qd_indicators")
        ni = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_signals")
        ns = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qd_signals WHERE pushed = true")
        ns_ok = cur.fetchone()[0]
        cur.execute("SELECT count(*), count(DISTINCT code) FROM qd_pricevol")
        npv, upv = cur.fetchone()
        con2.close()

        summary = (
            f"[e2e real] 完成 {datetime.now().strftime('%H:%M:%S')}\n"
            f"K线1m: {nk1} (code={uk1})  价量: {npv} (code={upv})\n"
            f"指标: {ni}  信号: {ns}  推送: {ns_ok}"
        )
        logger.info(summary)
        requests.post(WEBHOOK, json={
            "msg_type": "text",
            "content": {"text": summary}
        }, timeout=10)
    finally:
        try: con.close()
        except: pass

    tq.close()
    logger.info('🏁 e2e real 全部完成')


if __name__ == '__main__':
    main()
