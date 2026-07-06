-- ============================================================
-- 表名: qd_ladder_tracker
-- 用途: 打板梯队 + 2进3 晋级监控 (k4_ladder_tracker 写入, 5min/轮)
-- 数据源: qd_stock_snapshot (FCAmo/FCb/fHSL) + qd_stock_daily (ConZAFDateNum/LastStartZT) +
--         qd_stock_gpjy (gp40_lb_rate/gp39_next_red_rate/gp14_break_cnt) +
--         qd_sector_flow (板块强度)
-- 时间戳: snapshot_time (分钟对齐)
-- 说明:
--   连板全景: 按板数分组的全部连板股票 (首板/2板/3板/4板/5板+)
--   2进3 重点: 今日 2 连板股票按晋级概率评分, Top 5 带健康度标签
--   板块共振: 2进3 标的对应板块强度
--   连板数算法: qd_stock_daily.ConZAFDateNum (连续涨停天数, 含昨) +
--              今日 FCAmo>0 检测 → ConZAFDateNum+1(昨涨停今继续) 或 1(今首板)
-- 去重: DEDUP UPSERT KEYS(snapshot_time)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_ladder_tracker (
    snapshot_time       TIMESTAMP,

    -- 连板全景: 按板数分组 {1:[], 2:[], 3:[], 4:[], 5+:[]}
    lb_tiers            STRING,

    -- 2进3 晋级评分排行 Top 5
    promotion_rankings  STRING,

    -- 板块共振: 2进3 标的对应板块强度
    sector_resonance    STRING,

    -- 统计数据
    stats               STRING,   -- {total_zt, total_1b, total_2b, ..., candidates_2to3}

    -- 元信息
    calc_duration_ms    INT
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time);
