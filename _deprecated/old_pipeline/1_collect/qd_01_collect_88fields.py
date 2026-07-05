"""QuestDB 实时采集 - 全市场 88 字段 (50秒/轮)
参考 K:\DB数据库_v2\1_入库\101_jb_api_plhqL2kz_88zd.py
"""
import sys, os, time, json, io
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq

import psycopg2
import pandas as pd
from loguru import logger

# ============== 配置 ==============
QDB = dict(
    host=os.environ['QDB_HOST'],
    port=int(os.environ['QDB_PORT']),
    user=os.environ['QDB_USER'],
    password=os.environ['QDB_PASSWORD'],
    dbname=os.environ['QDB_DBNAME'],
)

TABLE = 'qd_snapshots_full'
INTERVAL_SEC = 50
BATCH_SIZE = 500
LOG_DIR = Path(__file__).resolve().parent.parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logger.add(LOG_DIR / 'qd_01_{time:YYYYMMDD}.log', rotation='1 day', retention='7 days')

# 字段顺序 (与 DDL qd_snapshots_full 严格一致)
# 注: 原计划 INT 的字段(连板数等)统一改 DOUBLE, 因 tq 88 字段可能返回 0.92 之类小数
INSERT_COLUMNS = [
    'code', 'snapshot_time', 'HqDate', 'stock_type',
    'ZTPrice', 'DTPrice', 'FzAmo', 'ZAF', 'VOpenZAF',
    'ZAFYesterday', 'ZAFPre2D', 'ZAFPre5', 'ZAFPre10',
    'ZAFPre20', 'ZAFPre30', 'ZAFPre60', 'ZAFYear',
    'ZAFPreMyMonth', 'ZAFPreOneYear',
    'TotalBVol', 'TotalSVol', 'FCAmo', 'BCancel',
    'SCancel', 'L2TicNum', 'L2OrderNum',
    'OpenZAF', 'OpenAmo', 'OpenZTBuy', 'OpenAmoPre1', 'OpenVolPre1',
    'CJJEPre1', 'CJJEPre3', 'FDEPre1', 'FDEPre2',
    'fLianB', 'LastStartZT', 'LastZTHzNum', 'ZTGPNum',
    'EverZTCount', 'YearZTDay', 'ConZAFDateNum',
    'HisHigh', 'HisLow', 'IPO_Price', 'MainBusiness',
    'DynaPE', 'MorePE', 'StaticPE_TTM', 'DYRatio',
    'PB_MRQ', 'BetaValue', 'TPFlag', 'IsT0Fund',
    'IsZCZGP', 'IsKzz', 'Kzz_HSCode', 'QHMainYYMM',
    'Yield', 'FreeLtgb', 'vzangsu', 'Wtb',
    'fetch_time',
]

# 数值列: 全部用 float
NUMERIC_COLS = set(INSERT_COLUMNS) - {
    'code', 'snapshot_time', 'HqDate', 'stock_type', 'fetch_time',
    'LastStartZT', 'MainBusiness', 'TPFlag', 'IsT0Fund', 'IsZCZGP',
    'IsKzz', 'Kzz_HSCode', 'QHMainYYMM',
}


# ============== 工具 ==============
def connect():
    return psycopg2.connect(**QDB)


def stock_code_to_tdx(code: str) -> str:
    if code.endswith('.SH') or code.endswith('.SZ'):
        return code
    if code.startswith(('88', '9')):
        return code
    if code.startswith('6'):
        return f'{code}.SH'
    return f'{code}.SZ'


def _cv(v, caster):
    if v is None or v == '' or v == '--':
        return None
    try:
        return caster(v)
    except (TypeError, ValueError):
        return None


def parse_row(code, stock_type, data, fetch_time):
    """按 INSERT_COLUMNS 顺序取值"""
    out = []
    for col in INSERT_COLUMNS:
        if col == 'code':
            out.append(code)
        elif col == 'snapshot_time':
            out.append(fetch_time)
        elif col == 'HqDate':
            v = data.get('HqDate')
            if v and len(str(v)) == 8 and str(v).isdigit():
                out.append(datetime.strptime(str(v), '%Y%m%d').date())
            else:
                out.append(None)
        elif col == 'stock_type':
            out.append(stock_type)
        elif col == 'fetch_time':
            out.append(fetch_time)
        elif col in NUMERIC_COLS:
            out.append(_cv(data.get(col), float))
        else:
            # 字符串列
            v = data.get(col)
            out.append(None if v is None else str(v))
    return tuple(out)


def copy_to_qdb(con, rows):
    """批量写入 (QuestDB 不支持 PG WITH 选项, 用 executemany 简单可靠)"""
    if not rows:
        return
    placeholders = ','.join(['%s'] * len(INSERT_COLUMNS))
    sql = f"INSERT INTO {TABLE} ({','.join(INSERT_COLUMNS)}) VALUES ({placeholders})"
    cur = con.cursor()
    try:
        cur.executemany(sql, rows)
        con.commit()
    except Exception as e:
        con.rollback()
        logger.error(f'INSERT 失败: {e}')
        raise


# ============== 采集主逻辑 ==============
def get_all_codes():
    sectors = tq.get_sector_list()
    logger.info(f'板块: {len(sectors)}')

    all_stocks = set()
    for s in sectors:
        try:
            codes = tq.get_stock_list_in_sector(s)
            if codes:
                all_stocks.update(codes)
        except Exception as e:
            logger.warning(f'板块 {s} 取股票失败: {e}')

    stocks = sorted(all_stocks)
    all_codes = stocks + sectors
    logger.info(f'股票 {len(stocks)} + 板块 {len(sectors)} = 总 {len(all_codes)}')
    return all_codes, stocks


def fetch_one_round(con, all_codes, stocks):
    fetch_time = datetime.now()
    stocks_set = set(stocks)
    rows = []
    failed = []
    start = time.time()

    for i, code in enumerate(all_codes):
        try:
            tdx = stock_code_to_tdx(code)
            data = tq.get_more_info(tdx, field_list=[])
            if data:
                stock_type = 'stock' if code in stocks_set else 'sector'
                rows.append(parse_row(code, stock_type, data, fetch_time))
        except Exception as e:
            failed.append((code, str(e)[:50]))

        if len(rows) >= BATCH_SIZE:
            copy_to_qdb(con, rows)
            rows.clear()

        if (i + 1) % 500 == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            logger.info(f'  进度 {i+1}/{len(all_codes)} ({rate:.1f}/秒)')

    if rows:
        copy_to_qdb(con, rows)

    elapsed = time.time() - start
    logger.info(f'本轮: 成功 {len(all_codes) - len(failed)} 失败 {len(failed)} 耗时 {elapsed:.1f}s')

    if failed:
        fail_path = LOG_DIR / f'failed_{fetch_time.strftime("%Y%m%d_%H%M%S")}.json'
        with open(fail_path, 'w', encoding='utf-8') as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)


def main():
    logger.info(f'▶ 启动 {TABLE} (间隔 {INTERVAL_SEC}s)')
    tq.initialize(os.path.abspath(__file__))
    try:
        all_codes, stocks = get_all_codes()
        con = connect()
        while True:
            loop_start = time.time()
            try:
                fetch_one_round(con, all_codes, stocks)
            except Exception as e:
                logger.error(f'轮询异常: {e}')
            elapsed = time.time() - loop_start
            sleep_sec = max(1, INTERVAL_SEC - int(elapsed))
            logger.info(f'睡 {sleep_sec}s')
            time.sleep(sleep_sec)
    finally:
        try:
            tq.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
