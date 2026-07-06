-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\12_lhb.sql
-- 用途: 2 张龙虎榜表 (龙虎榜明细 + 营业部画像)
-- 数据源: tqcenter get_lhb_detail + 东方财富龙虎榜接口
-- 时间戳: lhb_date / update_time
-- 营业部识别: config/broker_list.py 的 FAMOUS_BROKERS / BROKER_LABELS / BROKER_TYPE
-- 去重: DEDUP UPSERT KEYS(*_date/time, code/broker_name)
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_lhb_detail
-- 用途: 龙虎榜明细 (每日龙虎榜上榜个股 + 买卖营业部)
-- 数据源: tqcenter get_lhb_detail / 东方财富龙虎榜
-- 时间戳: lhb_date (龙虎榜日期)
-- 字段映射:
--   code         ← 上榜股票代码
--   lhb_date     ← 龙虎榜日期 (收盘后公布)
--   reason       ← 上榜原因 (涨幅偏离/振幅/换手率等)
--   rank         ← 上榜排名
--   buy_amount   ← 买入金额 (元)
--   sell_amount  ← 卖出金额 (元)
--   net_amount   ← 净额 (买-卖, 元)
--   broker_name  ← 营业部名称
--   broker_type  ← 营业部类型 (hot_money/institution/north, 来自 broker_list.py)
--   broker_label ← 营业部标签 (拉萨天团/杭州游资/机构 等, 来自 BROKER_LABELS)
-- 去重: (lhb_date, code, broker_name)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_lhb_detail (
    code         VARCHAR,
    lhb_date     TIMESTAMP,
    reason       VARCHAR,
    rank         INT,
    buy_amount   DOUBLE,
    sell_amount  DOUBLE,
    net_amount   DOUBLE,
    broker_name  VARCHAR,
    broker_type  VARCHAR,
    broker_label VARCHAR
) TIMESTAMP(lhb_date) PARTITION BY MONTH
DEDUP UPSERT KEYS(lhb_date, code, broker_name);


-- ------------------------------------------------------------
-- 表名: qd_lhb_broker
-- 用途: 营业部画像 (30 日统计 + 热度评级)
-- 数据源: 由 compute 模块基于 qd_lhb_detail 聚合计算
-- 时间戳: update_time (画像更新时刻)
-- 字段映射:
--   broker_name      ← 营业部名称
--   update_time      ← datetime.now()
--   broker_type      ← 营业部类型 (hot_money/institution/north)
--   broker_label     ← 营业部标签
--   total_buy_30d    ← 30 日累计买入额 (元)
--   total_sell_30d   ← 30 日累计卖出额 (元)
--   appear_count_30d ← 30 日上榜次数
--   hot_level        ← 热度评级 (1-5, 5 为最热)
-- 去重: (update_time, broker_name)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_lhb_broker (
    broker_name       VARCHAR,
    update_time       TIMESTAMP,
    broker_type       VARCHAR,
    broker_label      VARCHAR,
    total_buy_30d     DOUBLE,
    total_sell_30d    DOUBLE,
    appear_count_30d  INT,
    hot_level         INT
) TIMESTAMP(update_time) PARTITION BY MONTH
DEDUP UPSERT KEYS(update_time, broker_name);
