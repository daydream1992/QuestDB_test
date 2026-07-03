-- ============================================================
-- 骗炮识别 (k4_pianpao 盘后写入)
-- 移植自 DB数据库_v2 4_工具/pianpao_engine (简版, 完整库见 TODO)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_pianpao_daily (
    date        TIMESTAMP,
    code        VARCHAR,
    trap_cnt    INT,         -- 近 60 天骗炮次数 (一票否决依据)
    last_trap   TIMESTAMP    -- 最近一次骗炮日期
) TIMESTAMP(date) PARTITION BY MONTH
DEDUP UPSERT KEYS(date, code);
