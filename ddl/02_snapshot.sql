-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\02_snapshot.sql
-- 用途: 3 类标的的盘中高频快照 (10s/轮 重点标的, 60s/轮 全场轮换)
-- 数据源: tqcenter get_market_snapshot + get_more_info (盘中高频字段)
-- 时间戳: snapshot_time (datetime.now() 采集时刻)
-- 字段映射来源: config/fields.py 的 STOCK_SNAPSHOT_FIELDS / SECTOR_SNAPSHOT_FIELDS
-- 去重: DEDUP UPSERT KEYS(snapshot_time, code)
-- 备注: 个股 5 档买卖盘展开为 Buyp1-5 / Buyv1-5 / Sellp1-5 / Sellv1-5
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_stock_snapshot
-- 用途: 个股高频快照 (含 5 档买卖盘 + intraday 高频字段)
-- 数据源: get_market_snapshot (c2) + get_more_info intraday (c3)
-- 时间戳: snapshot_time (datetime.now(), 实时时间戳)
-- 去重: (snapshot_time, code)
-- 5档映射: Buyp[] → Buyp1..Buyp5, Buyv[] → Buyv1..Buyv5, Sellp[] → Sellp1..Sellp5, Sellv[] → Sellv1..Sellv5
-- 说明: ZAF/fHSL/Zjl 等字段来自 get_more_info intraday 模式, 通过 c3 补写入本表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_stock_snapshot (
    code            VARCHAR,
    code_type       SYMBOL,
    snapshot_time   TIMESTAMP,
    ItemNum         DOUBLE,
    LastClose       DOUBLE,
    Open            DOUBLE,
    Max             DOUBLE,
    Min             DOUBLE,
    Now             DOUBLE,
    Volume          BIGINT,
    NowVol          BIGINT,
    Amount          DOUBLE,
    Inside          BIGINT,
    Outside         BIGINT,
    TickDiff        DOUBLE,
    InOutFlag       INT,
    Jjjz            DOUBLE,
    Average         DOUBLE,
    XsFlag          INT,
    UpHome          INT,
    DownHome        INT,
    Before5MinNow   DOUBLE,
    Zangsu          DOUBLE,
    ZAFPre3         DOUBLE,
    Buyp1           DOUBLE,
    Buyp2           DOUBLE,
    Buyp3           DOUBLE,
    Buyp4           DOUBLE,
    Buyp5           DOUBLE,
    Buyv1           BIGINT,
    Buyv2           BIGINT,
    Buyv3           BIGINT,
    Buyv4           BIGINT,
    Buyv5           BIGINT,
    Sellp1          DOUBLE,
    Sellp2          DOUBLE,
    Sellp3          DOUBLE,
    Sellp4          DOUBLE,
    Sellp5          DOUBLE,
    Sellv1          BIGINT,
    Sellv2          BIGINT,
    Sellv3          BIGINT,
    Sellv4          BIGINT,
    Sellv5          BIGINT,
    -- 以下为 intraday 高频字段 (来自 get_more_info intraday 模式)
    ZTPrice         DOUBLE,
    DTPrice         DOUBLE,
    ZAF             DOUBLE,
    fHSL            DOUBLE,
    Fzhsl           DOUBLE,
    FzAmo           DOUBLE,
    Zjl             DOUBLE,
    Zjl_HB          DOUBLE,
    FCAmo           DOUBLE,
    FCb             DOUBLE,
    vzangsu         DOUBLE,
    ZTGPNum         DOUBLE,
    fLianB          DOUBLE,
    MA5Value        DOUBLE,
    Wtb             DOUBLE,
    LastStartZT     VARCHAR
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);


-- ------------------------------------------------------------
-- 表名: qd_sector_snapshot
-- 用途: 板块高频快照 (~21 字段, 无 5 档)
-- 数据源: get_market_snapshot (板块)
-- 时间戳: snapshot_time
-- 去重: (snapshot_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_sector_snapshot (
    code            VARCHAR,
    code_type       SYMBOL,
    snapshot_time   TIMESTAMP,
    ItemNum         DOUBLE,
    LastClose       DOUBLE,
    Open            DOUBLE,
    Max             DOUBLE,
    Min             DOUBLE,
    Now             DOUBLE,
    Volume          BIGINT,
    NowVol          BIGINT,
    Amount          DOUBLE,
    Inside          BIGINT,
    Outside         BIGINT,
    TickDiff        DOUBLE,
    InOutFlag       INT,
    Average         DOUBLE,
    XsFlag          INT,
    UpHome          INT,
    DownHome        INT,
    Before5MinNow   DOUBLE,
    Zangsu          DOUBLE,
    ZAFPre3         DOUBLE,
    Jjjz            DOUBLE
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);


-- ------------------------------------------------------------
-- 表名: qd_index_snapshot
-- 用途: 指数高频快照 (~11 字段, 无 5 档/无内外卖盘)
-- 数据源: get_market_snapshot (指数)
-- 时间戳: snapshot_time
-- 去重: (snapshot_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_index_snapshot (
    code            VARCHAR,
    code_type       SYMBOL,
    snapshot_time   TIMESTAMP,
    ItemNum         DOUBLE,
    LastClose       DOUBLE,
    Open            DOUBLE,
    Max             DOUBLE,
    Min             DOUBLE,
    Now             DOUBLE,
    Volume          BIGINT,
    Amount          DOUBLE,
    Average         DOUBLE,
    TickDiff        DOUBLE,
    ZAFPre3         DOUBLE
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);
