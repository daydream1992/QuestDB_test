r"""一键重建全部 QuestDB 表

执行: python K:\QuestDB_test\ddl\_reset_all.py
"""
import os, sys, re, time
from pathlib import Path
import psycopg2
from loguru import logger

# 项目根目录
PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJ_ROOT / 'config' / '.env')

QDB = dict(
    host=os.environ['QDB_HOST'],
    port=int(os.environ['QDB_PORT']),
    user=os.environ['QDB_USER'],
    password=os.environ['QDB_PASSWORD'],
    dbname=os.environ['QDB_DBNAME'],
)

LOG_DIR = PROJ_ROOT / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / 'reset_all_{time:YYYYMMDD}.log', rotation='50 MB', retention='30 days')

# DDL 文件顺序
DDL_FILES = [
    '00_registry.sql',
    '01_daily.sql',
    '02_snapshot.sql',
    '03_pricevol.sql',
    '04_kline.sql',
    '05_indicators.sql',
    '06_signals.sql',
    '07_relation.sql',
    '08_flow.sql',
    '09_resonance.sql',
    '10_auction.sql',
    '11_big_order.sql',
    '12_lhb.sql',
    '13_sentiment.sql',
    '14_intraday_event.sql',
    '16_stock_intraday.sql',
    '17_stock_gpjy.sql',
    '18_sentiment_deep.sql',
    '19_sector_heatmap.sql',
    '20_ladder_tracker.sql',
    '22_alpha_score.sql',
    '23_positions_v2.sql',
]

def main():
    ddl_dir = Path(__file__).resolve().parent
    logger.info(f'=== 一键重建 QuestDB 表 ===')
    logger.info(f'DDL 目录: {ddl_dir}')
    logger.info(f'QuestDB: {QDB["host"]}:{QDB["port"]}')

    con = psycopg2.connect(**QDB)
    con.autocommit = True
    cur = con.cursor()

    # 先 DROP 所有 qd_ 开头的表 (强制重建, 避免 IF NOT EXISTS 跳过旧结构)
    confirm = input(f'确认 DROP {len(old_tables)} 张 qd_ 表? [y/N] ').strip().lower()
    if confirm != 'y' and confirm != 'yes':
        logger.info('用户取消 DROP')
        return
    cur.execute("SELECT table_name FROM tables() WHERE table_name LIKE 'qd_%'")
    old_tables = [r[0] for r in cur.fetchall()]
    if old_tables:
        logger.info(f'=== DROP {len(old_tables)} 张旧表 ===')
        for t in old_tables:
            try:
                cur.execute(f'DROP TABLE {t}')
                logger.info(f'  DROP OK: {t}')
            except Exception as e:
                logger.error(f'  DROP ERR {t}: {str(e)[:100]}')

    ok = err = 0
    for fname in DDL_FILES:
        fpath = ddl_dir / fname
        if not fpath.exists():
            logger.warning(f'跳过(不存在): {fname}')
            continue
        sql_text = fpath.read_text(encoding='utf-8')
        # 去掉 SQL 行注释 (-- 到行尾) 再分割, 避免注释内的 ; 切碎语句
        cleaned = re.sub(r'--.*$', '', sql_text, flags=re.MULTILINE)
        for stmt in cleaned.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                cur.execute(stmt)
                first80 = stmt.split('\n')[0].strip()[:80]
                logger.info(f'OK: {first80}')
                ok += 1
            except Exception as e:
                logger.error(f'ERR: {str(e)[:120]}')
                err += 1

    # 验证
    cur.execute("SELECT table_name FROM tables() WHERE table_name LIKE 'qd_%' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    logger.info(f'=== 完成: {ok} OK, {err} ERR ===')
    logger.info(f'qd_ 表 ({len(tables)} 张):')
    for t in tables:
        logger.info(f'  {t}')

    # === 修复: ALTER TABLE 补缺失列 (CREATE TABLE IF NOT EXISTS 不会补旧表) ===
    # code_type 是这次改造加的新列, 旧表不会自动有
    logger.info('=== 检查缺失列 ===')
    _REPAIR = [
        ('qd_stock_snapshot',  'code_type', 'SYMBOL'),
        ('qd_sector_snapshot', 'code_type', 'SYMBOL'),
        ('qd_index_snapshot',  'code_type', 'SYMBOL'),
        ('qd_stock_intraday',  'code_type', 'SYMBOL'),
        ('qd_stock_daily',     'code_type', 'SYMBOL'),
        ('qd_sector_daily',    'code_type', 'SYMBOL'),
        ('qd_index_daily',     'code_type', 'SYMBOL'),
    ]
    _repair_fixed = 0
    for table, col, dtype in _REPAIR:
        if table in tables:
            try:
                cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {dtype}')
                logger.info(f'  补全: {table}.{col} ({dtype})')
                _repair_fixed += 1
            except Exception as e:
                if 'already exists' in str(e).lower():
                    continue
                logger.warning(f'  补全失败 {table}.{col}: {str(e)[:80]}')
    if _repair_fixed:
        logger.info(f'补全 {_repair_fixed} 列')
    logger.info('=== 修复完成 ===')

    con.close()

if __name__ == '__main__':
    main()
