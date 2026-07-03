-- ============================================================
-- 盘中异动事件 (intraday_engine 写入, 实盘订阅模块)
-- 移植自 DB数据库_v2 01实盘监控 (events parquet), 改写为 QuestDB
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_intraday_event (
    event_time   TIMESTAMP,
    code         VARCHAR,
    event_type   VARCHAR,   -- surge_up/surge_down/limit_seal/limit_break/capital_in/capital_out
    description  VARCHAR,
    critical     BOOLEAN
) TIMESTAMP(event_time) PARTITION BY DAY
DEDUP UPSERT KEYS(event_time, code, event_type);
