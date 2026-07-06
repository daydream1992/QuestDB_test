-- ============================================================
-- 表名: qd_auction_snapshot
-- 脚本路径: K:\QuestDB_test\ddl\10_auction.sql
-- 用途: 集合竞价快照 (开盘竞价 9:20-9:25 / 尾盘竞价 14:57-15:00)
-- 数据源: tqcenter get_market_snapshot (竞价时段特殊采集)
-- 时间戳: auction_time (竞价快照时刻)
-- 字段映射:
--   code            ← 标的代码
--   auction_time    ← datetime.now() (竞价采集时刻)
--   auction_price   ← 竞价价格 (撮合参考价)
--   auction_volume  ← 竞价成交量
--   auction_amount  ← 竞价成交额
--   gap_pct         ← 缺口百分比 (相对前收盘)
--   auction_type    ← 竞价类型 (open/close)
--   prev_close      ← 前一日收盘价 (用于计算 gap_pct)
-- 去重: DEDUP UPSERT KEYS(auction_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_auction_snapshot (
    code            VARCHAR,
    auction_time    TIMESTAMP,
    auction_price   DOUBLE,
    auction_volume  BIGINT,
    auction_amount  DOUBLE,
    gap_pct         DOUBLE,
    auction_type    VARCHAR,
    prev_close      DOUBLE
) TIMESTAMP(auction_time) PARTITION BY DAY
DEDUP UPSERT KEYS(auction_time, code);
