-- ============================================================
-- 表名: qd_sentiment_deep
-- 用途: 深度情绪分析 (k4_sentiment 写入, 5min/轮)
-- 数据源: QuestDB 已有表 (全读库, 不读 tqcenter)
-- 时间戳: snapshot_time (分钟对齐)
-- 说明:
--   8 维度全局深度情绪, 覆盖 k3 不做的部分 (恐慌/贪婪指数, 资金情绪,
--   多空比, 板块轮动强度, 情绪周期, 连板梯队健康度, 背离综合, 情绪均线)
--   预留所有列, 逐步填充 (当前 Phase 2 实现 D1/D5/D7)
-- 去重: DEDUP UPSERT KEYS(snapshot_time)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_sentiment_deep (
    snapshot_time          TIMESTAMP,     -- 分钟对齐, 同分钟去重

    -- D1: 恐慌/贪婪指数 (Phase 2)
    pg_index               DOUBLE,        -- 0-100 恐慌贪婪指数
    pg_signal              VARCHAR,       -- 恐慌/恐惧/中性/贪婪/狂热

    -- D2: 多空比 (预留)
    bb_ratio               DOUBLE,        -- 多空比
    bb_signal              VARCHAR,       -- 偏多/偏空/中性

    -- D3: 板块轮动强度 (预留)
    rotation_intensity     DOUBLE,        -- 0-100 轮动强度
    rotation_signal        VARCHAR,       -- 热点聚焦/正常/快速轮动/电风扇

    -- D4: 短线情绪周期 (预留)
    cycle_phase            VARCHAR,       -- 冰点/复苏/回暖/高潮/分化/退潮
    phase_confidence       DOUBLE,        -- 0-1 置信度

    -- D5: 资金情绪 (Phase 2)
    capital_sentiment      DOUBLE,        -- -100 ~ +100 资金情绪
    market_main_net        DOUBLE,        -- 全市场主力净流总和 (万元)
    capital_consistency    DOUBLE,        -- 主力方向一致性 (0-1)
    dark_money_active      DOUBLE,        -- 暗资金活跃度

    -- D6: 连板梯队健康度 (预留)
    ladder_health          DOUBLE,        -- 0-100
    ladder_signal          VARCHAR,       -- 健康/一般/危险

    -- D7: 背离综合 (Phase 2)
    divergence_count       INT,           -- 当前背离数量
    divergences            STRING,        -- 背离详情 JSON

    -- D8: 情绪均线 (预留, 基于 snapshot_min 历史帧)
    st_ma_zt_cnt_5         DOUBLE,        -- zt_cnt MA5
    st_ma_zt_cnt_10        DOUBLE,        -- zt_cnt MA10
    st_ma_fbl_5            DOUBLE,        -- fbl MA5
    st_ma_fbl_10           DOUBLE,        -- fbl MA10
    st_ma_udr_5            DOUBLE,        -- udr MA5
    st_ma_udr_10           DOUBLE,        -- udr MA10
    st_ma_pg_5             DOUBLE,        -- pg_index MA5 (自引用)
    st_ma_pg_10            DOUBLE,        -- pg_index MA10

    -- 元信息
    calc_duration_ms       INT            -- 本次计算耗时 (ms)
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time);
