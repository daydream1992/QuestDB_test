-- ============================================================
-- 表名: qd_sector_heatmap
-- 用途: 板块热力图 + 最强个股梯队 (k4_sector_heatmap 写入, 5min/轮)
-- 数据源: qd_sector_snapshot + qd_stock_snapshot + relation_graph
-- 时间戳: snapshot_time (分钟对齐)
-- 说明:
--   4 组板块排行 (行业一级/行业二级/行业三级/概念 各 Top 5)
--   + 每组最强板块的个股梯队 (Top 3)
--   全部存为 STRING JSON, 飞书推送时解析组装为易读文本
-- 去重: DEDUP UPSERT KEYS(snapshot_time)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_sector_heatmap (
    snapshot_time       TIMESTAMP,

    -- 4 组板块排行 (每组 Top 5, JSON)
    industry_l1_ranking STRING,   -- [{name, zaf, zt_count}, ...]
    industry_l2_ranking STRING,
    industry_l3_ranking STRING,
    concept_ranking     STRING,

    -- 每组最强板块的个股梯队 (Top 3, JSON)
    industry_l1_stocks  STRING,   -- [{code, name, zaf, is_zt}, ...]
    industry_l2_stocks  STRING,
    industry_l3_stocks  STRING,
    concept_stocks      STRING,

    -- 元信息
    calc_duration_ms    INT
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time);
