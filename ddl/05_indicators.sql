-- ============================================================
-- 表名: qd_indicators
-- 脚本路径: K:\QuestDB_prod\ddl\05_indicators.sql
-- 用途: 技术指标计算结果 (MACD / 压力支撑 / 布林带 / 均线)
-- 数据源: 由 compute 模块基于 qd_kline_*/qd_*_snapshot 计算
-- 时间戳: calc_time (计算时刻)
-- 字段映射:
--   code           ← 标的代码
--   calc_time      ← datetime.now() (计算时刻)
--   close          ← 最新收盘/现价
--   macd_dif       ← MACD DIF 线
--   macd_dea       ← MACD DEA 线
--   macd_hist      ← MACD 柱 (2*(DIF-DEA))
--   pressure_high  ← 近期压力位 (前高/密集成交区)
--   support_low    ← 近期支撑位 (前低/密集成交区)
--   boll_upper     ← 布林带上轨
--   boll_mid       ← 布林带中轨
--   boll_lower     ← 布林带下轨
--   ma5            ← 5 日均线
--   ma10           ← 10 日均线
--   ma20           ← 20 日均线
-- 去重: DEDUP UPSERT KEYS(calc_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_indicators (
    code           VARCHAR,
    calc_time      TIMESTAMP,
    close          DOUBLE,
    macd_dif       DOUBLE,
    macd_dea       DOUBLE,
    macd_hist      DOUBLE,
    pressure_high  DOUBLE,
    support_low    DOUBLE,
    boll_upper     DOUBLE,
    boll_mid       DOUBLE,
    boll_lower     DOUBLE,
    ma5            DOUBLE,
    ma10           DOUBLE,
    ma20           DOUBLE
) TIMESTAMP(calc_time) PARTITION BY DAY
DEDUP UPSERT KEYS(calc_time, code);
