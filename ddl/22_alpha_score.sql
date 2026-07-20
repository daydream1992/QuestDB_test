-- 22_alpha_score.sql
-- 因子 alpha 快照表 (每轮 alpha_df 落库, 供回测/复盘/IC 分析)
-- 写入: 无活跃写入方 (2026-07-14 compute/factor_store.py 移 _deprecated/;
--       compute/alpha_engine.py 仅纯内存计算 ctx.alpha_df)
-- 表状态: 休眠表 (保留 schema 供后续回测/IC 分析启用)

CREATE TABLE IF NOT EXISTS qd_alpha_score (
    calc_time     TIMESTAMP,
    code          VARCHAR,
    alpha_score   DOUBLE,        -- 加权后的总分 (z-score 量级)
    rank          INT,           -- 全市场排名 (1=最强)
    decile        INT,           -- 十分位 (0-9, 9=最强; -1=未知)
    sector_rank   INT,           -- 板块内排名
    top_factors   VARCHAR,       -- 贡献最大的 3 个因子 (JSON 数组)
    -- 各因子原始归一化值 (动态扩展, 回测时按需 SELECT)
    ts_momentum_5m           DOUBLE,
    ts_acceleration          DOUBLE,
    xs_amount_surge          DOUBLE,
    xs_sector_strength       DOUBLE,
    micro_imbalance          DOUBLE,
    micro_imbalance_weighted DOUBLE,
    ladder_position          DOUBLE,
    quality_gp               DOUBLE
) TIMESTAMP(calc_time) PARTITION BY DAY
DEDUP UPSERT KEYS(calc_time, code);
