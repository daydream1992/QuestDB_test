-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\04_kline.sql
-- 用途: 2 个周期的 K 线数据 (1 分钟 / 5 分钟)
-- 数据源: tqcenter get_kline_data
-- 时间戳: kline_time (K 线周期时间戳)
-- 字段映射:
--   code        ← 标的代码
--   kline_time  ← K 线时间 (datetime)
--   open        ← 开盘价
--   high        ← 最高价
--   low         ← 最低价
--   close       ← 收盘价
--   volume      ← 成交量
--   amount      ← 成交额
-- 去重: DEDUP UPSERT KEYS(kline_time, code)
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_kline_1m
-- 用途: 1 分钟 K 线
-- 时间戳: kline_time
-- 去重: (kline_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_kline_1m (
    code        VARCHAR,
    kline_time  TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT,
    amount      DOUBLE
) TIMESTAMP(kline_time) PARTITION BY MONTH
DEDUP UPSERT KEYS(kline_time, code);


-- ------------------------------------------------------------
-- 表名: qd_kline_5m
-- 用途: 5 分钟 K 线
-- 时间戳: kline_time
-- 去重: (kline_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_kline_5m (
    code        VARCHAR,
    kline_time  TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT,
    amount      DOUBLE
) TIMESTAMP(kline_time) PARTITION BY MONTH
DEDUP UPSERT KEYS(kline_time, code);
