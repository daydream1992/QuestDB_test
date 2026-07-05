"""qd_04: 扫 indicators → 信号检测 → 飞书 Webhook 推送

信号规则(简单版, 测试项目先用):
  - golden_cross:   MACD 金叉 (DIF 上穿 DEA)
  - death_cross:    MACD 死叉 (DIF 下穿 DEA)
  - break_pressure: 突破 20 根压力位 (close >= pressure_high)
  - break_support:  跌破 20 根支撑位 (close <= support_low)

频控: 同 code+signal_type 5 分钟内只推一次 (qd_signal_log)

推送: 飞书自定义机器人 Webhook (text 类型, 默认值)
"""
import os
from pathlib import Path
from datetime import datetime, timedelta
import json
import requests
import psycopg2
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

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
logger.add(LOG_DIR / 'qd_04_{time:YYYYMMDD}.log', rotation='1 day', retention='7 days')

SRC_IND = 'qd_indicators'
DST_SIG = 'qd_signals'
LOG_TBL = 'qd_signal_log'

PUSH_COOLDOWN_SEC = 300  # 5 分钟同 code+type 频控

SIGNAL_LABELS = {
    'golden_cross':   'MACD 金叉',
    'death_cross':    'MACD 死叉',
    'break_pressure': '突破压力位',
    'break_support':  '跌破支撑位',
}


def connect():
    """QuestDB 9.4.3 PG 协议存在事务快照延迟, 用 autocommit=True 避免"""
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def fetch_indicators(con) -> pd.DataFrame:
    sql = f"""
    SELECT code, indicator_time, close,
           macd_dif, macd_dea, macd_hist,
           pressure_high, support_low
    FROM {SRC_IND}
    ORDER BY code, indicator_time
    """
    return pd.read_sql(sql, con)


def detect_signals(df: pd.DataFrame) -> list:
    """按 code 分组扫金叉/死叉/突破/跌破"""
    signals = []
    for code, g in df.groupby('code'):
        g = g.sort_values('indicator_time').reset_index(drop=True)
        if len(g) < 2:
            continue
        for i in range(1, len(g)):
            prev = g.iloc[i - 1]
            cur = g.iloc[i]
            t = cur['indicator_time']
            # 金叉
            if prev['macd_dif'] <= prev['macd_dea'] and cur['macd_dif'] > cur['macd_dea']:
                signals.append(_build('golden_cross', code, t, cur, prev))
            # 死叉
            if prev['macd_dif'] >= prev['macd_dea'] and cur['macd_dif'] < cur['macd_dea']:
                signals.append(_build('death_cross', code, t, cur, prev))
            # 突破压力
            pres = cur['pressure_high']
            if pres and cur['close'] is not None and cur['close'] >= pres and prev['close'] < prev['pressure_high']:
                signals.append(_build('break_pressure', code, t, cur, prev))
            # 跌破支撑
            supp = cur['support_low']
            if supp and cur['close'] is not None and cur['close'] <= supp and prev['close'] > prev['support_low']:
                signals.append(_build('break_support', code, t, cur, prev))
    return signals


def _build(signal_type, code, t, cur, prev) -> dict:
    return {
        'signal_time': t.to_pydatetime() if hasattr(t, 'to_pydatetime') else t,
        'code': code,
        'signal_type': signal_type,
        'severity': 1,
        'payload': json.dumps({
            'close': float(cur['close']) if cur['close'] is not None else None,
            'dif':   float(cur['macd_dif']),
            'dea':   float(cur['macd_dea']),
            'hist':  float(cur['macd_hist']),
            'pressure_high': float(cur['pressure_high']) if cur['pressure_high'] is not None else None,
            'support_low':   float(cur['support_low'])   if cur['support_low']   is not None else None,
        }, ensure_ascii=False),
        'pushed': False,
    }


def can_push(con, code, signal_type, now) -> bool:
    """同 code+signal_type 用 max(last_push) 判定频控 (兼容多行 log)"""
    cur = con.cursor()
    cur.execute(
        f"SELECT max(last_push) FROM {LOG_TBL} WHERE code=%s AND signal_type=%s",
        (code, signal_type),
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return True
    return (now - row[0]).total_seconds() >= PUSH_COOLDOWN_SEC


def mark_pushed(con, code, signal_type, now):
    cur = con.cursor()
    cur.execute(
        f"INSERT INTO {LOG_TBL} (code, signal_type, last_push) VALUES (%s, %s, %s)",
        (code, signal_type, now),
    )


def save_signal(con, sig, pushed: bool):
    cur = con.cursor()
    cur.execute(
        f"""INSERT INTO {DST_SIG}
            (signal_time, code, signal_type, severity, payload, pushed)
            VALUES (%s, %s, %s, %s, %s, %s)""",
        (sig['signal_time'], sig['code'], sig['signal_type'],
         sig['severity'], sig['payload'], pushed),
    )


def push_lark(sig: dict) -> bool:
    """飞书 text 推送"""
    p = json.loads(sig['payload'])
    label = SIGNAL_LABELS.get(sig['signal_type'], sig['signal_type'])
    text = (
        f"[QuestDB 信号] {label}\n"
        f"代码: {sig['code']}\n"
        f"时间: {sig['signal_time']}\n"
        f"现价: {p.get('close')}\n"
        f"MACD: DIF={p.get('dif'):.4f} DEA={p.get('dea'):.4f} HIST={p.get('hist'):.4f}\n"
        f"压力位: {p.get('pressure_high')}  支撑位: {p.get('support_low')}"
    )
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=10)
        ok = r.status_code == 200 and (r.json().get('code') == 0 or r.json().get('StatusCode') == 0)
        if not ok:
            logger.warning(f'飞书返回非 OK: status={r.status_code} body={r.text[:200]}')
        return ok
    except Exception as e:
        logger.error(f'飞书推送异常: {e}')
        return False


def run(con=None):
    logger.info('▶ qd_04 信号扫描开始')
    own = con is None
    if own:
        con = connect()
    try:
        df = fetch_indicators(con)
        logger.info(f'读到 {len(df)} 条指标')
        if df.empty:
            logger.warning('无指标, 跳过')
            return
        signals = detect_signals(df)
        logger.info(f'检出 {len(signals)} 个信号')
        now = datetime.now()
        pushed_n = 0
        skipped_n = 0
        for sig in signals:
            if not can_push(con, sig['code'], sig['signal_type'], now):
                logger.info(f'  频控跳过 {sig["code"]} {sig["signal_type"]}')
                skipped_n += 1
                # 频控内的也入库, 但不推送
                save_signal(con, sig, pushed=False)
                continue
            ok = push_lark(sig)
            save_signal(con, sig, pushed=ok)
            if ok:
                mark_pushed(con, sig['code'], sig['signal_type'], now)
                pushed_n += 1
            logger.info(f'  {sig["code"]} {sig["signal_type"]} pushed={ok}')
        logger.info(f'✓ qd_04 完成: 推送 {pushed_n} / 频控跳过 {skipped_n}')
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
