-- ============================================================
-- 表名: qd_focus_log
-- 用途: 重点池轮次快照 (每轮 1 行, 记录本轮选股器筛出的重点股)
-- 说明:
--   每轮由 intraday_loop 在 _get_focus_codes 后写入。
--   全天约 2000 行 (每 ~43s 一轮), 零负担。
--   查某只股票是否曾被选入: SELECT * FROM qd_focus_log WHERE focus_codes LIKE '%code%'
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_focus_log (
    snapshot_time   TIMESTAMP,
    focus_stock_count INT,          -- 重点股数量 (去重后)
    focus_all_count  INT,           -- 含板块/指数后的总数
    focus_stock_codes  STRING,      -- 重点股代码列表 JSON (供分析)
    focus_all_codes    STRING,      -- 含板块/指数 JSON
    selector_detail    STRING       -- 各维度数量 JSON: {top_change, top_volume, high_hsl, lianban, near_zt}
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time);
