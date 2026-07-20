-- ============================================================
-- 表名: qd_sector_linkage
-- 脚本路径: K:/QuestDB_test/ddl/26_sector_linkage.sql
-- 用途: 板块联动分析结果 (评分 + 个股角色 + 预警)
-- 数据源: compute/k6_linkage.py
-- 时间戳: linkage_time (分钟对齐)
-- 说明:
--   联动评分: 0-100 分，综合资金流/涨停/连板/龙头四个维度
--   个股角色: 龙头/中军/补涨/跟风 JSON 存储
--   预警信号: 机会(评分≥80+龙头≥5%) / 风险(评分≤30 OR 流出≥20亿)
-- 去重: DEDUP UPSERT KEYS(linkage_time, block_code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_sector_linkage (
    linkage_time       TIMESTAMP,
    block_code         VARCHAR,
    block_name         VARCHAR,
    block_type         VARCHAR,       -- industry_l1/l2/l3/concept/region/style/index

    -- 联动评分
    linkage_score      DOUBLE,        -- 0-100 联动强度
    net_inflow         DOUBLE,        -- 主力净流入 (元)
    zt_count           INT,           -- 涨停家数
    ladder_height      INT,           -- 最高连板高度

    -- 龙头信息
    leader_code        VARCHAR,       -- 龙头股代码
    leader_change      DOUBLE,        -- 龙头涨幅%

    -- 共振信息 (预留)
    resonance_sectors  STRING,        -- JSON: [{concept_code, overlap_pct}]

    -- 个股角色
    stock_roles        STRING,        -- JSON: {龙头:[], 中军:[], 补涨:[], 跟风:[]}

    -- 元信息
    calc_duration_ms   INT
) TIMESTAMP(linkage_time) PARTITION BY DAY
DEDUP UPSERT KEYS(linkage_time, block_code);
