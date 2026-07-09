-- ============================================================
-- 表名: qd_divergence
-- 脚本路径: K:\QuestDB_test\ddl\25_divergence.sql
-- 用途: 量价背离事件 (顶背离/底背离)
-- 数据源: strategy/volume_price_divergence.py
-- 时间戳: divergence_time
-- 字段映射:
--   code           ← 标的代码
--   divergence_time ← 背离检测时刻
--   direction      ← 背离方向 (top/底背离 / bottom/顶背离)
--   price_change   ← 价格变化率 (%)
--   volume_change  ← 成交量变化率 (%)
--   signal         ← 信号名称
--   reason         ← 原因描述
-- 去重: DEDUP UPSERT KEYS(divergence_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_divergence (
    code             VARCHAR,
    divergence_time  TIMESTAMP,
    direction        VARCHAR,
    price_change     DOUBLE,
    volume_change    DOUBLE,
    signal           VARCHAR,
    reason           VARCHAR
) TIMESTAMP(divergence_time) PARTITION BY DAY
DEDUP UPSERT KEYS(divergence_time, code);
