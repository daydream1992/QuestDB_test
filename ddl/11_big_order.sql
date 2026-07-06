-- ============================================================
-- 表名: qd_big_order
-- 脚本路径: K:\QuestDB_test\ddl\11_big_order.sql
-- 用途: L2 大单事件 (单笔超大单/大单成交记录)
-- 数据源: tqcenter get_big_order / L2 逐笔成交
-- 时间戳: order_time (大单成交时刻)
-- 字段映射:
--   code         ← 标的代码
--   order_time   ← 大单成交时间
--   order_type   ← 订单类型 (buy/sell)
--   price        ← 成交价
--   volume       ← 成交量 (股)
--   amount       ← 成交额 (元)
--   order_level  ← 大单级别 (big/huge/super, 对应大单/特大单/超大单)
--   broker       ← 所属营业部 (L2 可见时填充)
-- 去重: DEDUP UPSERT KEYS(order_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_big_order (
    code         VARCHAR,
    order_time   TIMESTAMP,
    order_type   VARCHAR,
    price        DOUBLE,
    volume       BIGINT,
    amount       DOUBLE,
    order_level  VARCHAR,
    broker       VARCHAR
) TIMESTAMP(order_time) PARTITION BY DAY
DEDUP UPSERT KEYS(order_time, code);
