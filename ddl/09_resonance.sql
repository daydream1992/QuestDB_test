-- ============================================================
-- 表名: qd_resonance
-- 脚本路径: K:\QuestDB_test\ddl\09_resonance.sql
-- 用途: 多层共振分析结果 (个股 / 板块 / 指数 / MACD / 量能 / 资金流 共振)
-- 数据源: 由 compute/resonance 模块基于多表数据综合计算
-- 时间戳: resonance_time (共振分析时刻)
-- 字段映射:
--   code              ← 标的代码
--   resonance_time    ← datetime.now()
--   sector_resonance  ← 板块共振分 (0-100, 板块强度与个股同步性)
--   index_resonance   ← 指数共振分 (0-100, 大盘配合度)
--   macd_resonance    ← MACD 共振分 (0-100, 多周期 MACD 同向性)
--   volume_resonance  ← 量能共振分 (0-100, 放量/缩量配合度)
--   flow_resonance    ← 资金流共振分 (0-100, 主力资金配合度)
--   total_score       ← 综合共振分 (加权总分 0-100)
--   signal_type       ← 信号类型 (strong_buy/buy/watch/sell)
--   description       ← 共振描述 (人类可读)
-- 去重: DEDUP UPSERT KEYS(resonance_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_resonance (
    code              VARCHAR,
    resonance_time    TIMESTAMP,
    sector_resonance  DOUBLE,
    index_resonance   DOUBLE,
    macd_resonance    DOUBLE,
    volume_resonance  DOUBLE,
    flow_resonance    DOUBLE,
    total_score       DOUBLE,
    signal_type       VARCHAR,
    description       VARCHAR
) TIMESTAMP(resonance_time) PARTITION BY DAY
DEDUP UPSERT KEYS(resonance_time, code);
