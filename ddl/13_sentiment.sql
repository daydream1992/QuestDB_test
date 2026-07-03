-- ============================================================
-- 大盘情绪监控三表 (k3_sentiment 写入)
-- 移植自 DB数据库_v2 00_大盘情绪监控 (SQLite sentiment.db), 改写为 QuestDB
-- 三层: 大盘层(定仓位) / 板块层(定方向) / 个股层6池(定标的)
-- ============================================================

-- 1) 分钟级情绪快照 (画情绪分时图, 每帧一行)
CREATE TABLE IF NOT EXISTS qd_sentiment_snapshot_min (
    snapshot_time   TIMESTAMP,
    emotion         VARCHAR,        -- 冰点/低迷/中性/活跃/过热
    emotion_order   INT,            -- 0-4 (跨帧比较用)
    zt_cnt          INT,            -- 涨停数 (focus 池口径)
    dt_cnt          INT,            -- 跌停数
    break_cnt       INT,            -- 炸板数
    fbl             DOUBLE,         -- 封板率 %
    max_lb          INT,            -- 最高连板
    udr             DOUBLE,         -- 涨跌比 (全场口径, pricevol)
    up_cnt          INT,            -- 涨家数 (全场)
    down_cnt        INT,            -- 跌家数 (全场)
    index_zaf       DOUBLE,         -- 主指数涨幅 % (上证)
    top_sectors     STRING,         -- Top 板块 JSON
    lb_tier         STRING          -- 连板梯队 JSON
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time);

-- 2) 变盘/跨越事件 (盘中拐点定位)
CREATE TABLE IF NOT EXISTS qd_sentiment_event_log (
    event_time      TIMESTAMP,
    event_type      VARCHAR,        -- turn_zt_drop / turn_udr_flip / emotion_crossing
    description     VARCHAR,
    detail          STRING          -- JSON 详情
) TIMESTAMP(event_time) PARTITION BY DAY
DEDUP UPSERT KEYS(event_time, event_type);

-- 3) 每日归档 (盘后 eod 写入, 批2 暂留表结构)
CREATE TABLE IF NOT EXISTS qd_sentiment_daily (
    date            TIMESTAMP,
    emotion         VARCHAR,
    zt_cnt_max      INT,
    fbl_avg         DOUBLE,
    max_lb          INT,
    summary         STRING          -- JSON 每日汇总
) TIMESTAMP(date) PARTITION BY MONTH
DEDUP UPSERT KEYS(date);
