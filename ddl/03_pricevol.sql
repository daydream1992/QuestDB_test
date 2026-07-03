-- ============================================================
-- 表名: qd_pricevol
-- 脚本路径: K:\QuestDB_prod\ddl\03_pricevol.sql
-- 用途: 全市场批量价量 (轻量高频, 10s/轮)
-- 数据源: tqcenter get_pricevol (1 次调用拿全场)
-- 时间戳: snapshot_time
-- 字段映射:
--   code         ← stock_list 中的代码
--   snapshot_time ← datetime.now()
--   last_close   ← LastClose (前收盘价)
--   now          ← Now (现价)
--   volume       ← Volume (累计成交量)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_pricevol (
    code           VARCHAR,
    snapshot_time  TIMESTAMP,
    last_close     DOUBLE,
    now            DOUBLE,
    volume         BIGINT
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);
