-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\01_daily.sql
-- 用途: 3 类标的的日级 more_info 数据 (每日收盘后/盘中定时写入)
-- 数据源: tqcenter get_more_info
-- 时间戳: date (由 HqDate 解析而来, 作为 designated timestamp)
-- 字段映射来源: config/fields.py 的 STOCK_DAILY_FIELDS / SECTOR_DAILY_FIELDS / INDEX_DAILY_FIELDS
-- 去重: DEDUP UPSERT KEYS(date, code) - 同一标的同一天仅保留最新
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_stock_daily
-- 用途: 个股日级 more_info (~50 字段)
-- 数据源: get_more_info (个股)
-- 时间戳: date ← HqDate (解析为 TIMESTAMP)
-- 去重: (date, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_stock_daily (
    code            VARCHAR,
    date            TIMESTAMP,
    HqDate          VARCHAR,
    ZTPrice         DOUBLE,
    DTPrice         DOUBLE,
    ZAFYesterday    DOUBLE,
    ZAFPre2D        DOUBLE,
    ZAFPre5         DOUBLE,
    ZAFPre10        DOUBLE,
    ZAFPre20        DOUBLE,
    ZAFPre30        DOUBLE,
    ZAFPre60        DOUBLE,
    ZAFYear         DOUBLE,
    ZAFPreMyMonth   DOUBLE,
    ZAFPreOneYear   DOUBLE,
    Zsz             DOUBLE,
    Ltsz            DOUBLE,
    DynaPE          DOUBLE,
    MorePE          DOUBLE,
    StaticPE_TTM    DOUBLE,
    DYRatio         DOUBLE,
    PB_MRQ          DOUBLE,
    Yield           DOUBLE,
    FreeLtgb        DOUBLE,
    BetaValue       DOUBLE,
    fLianB          DOUBLE,
    LastZTHzNum     DOUBLE,
    EverZTCount     DOUBLE,
    YearZTDay       DOUBLE,
    ConZAFDateNum   DOUBLE,
    LastStartZT     VARCHAR,
    ZTGPNum         DOUBLE,
    OpenAmo         DOUBLE,
    OpenAmoPre1     DOUBLE,
    OpenVolPre1     DOUBLE,
    CJJEPre1        DOUBLE,
    CJJEPre3        DOUBLE,
    FDEPre1         DOUBLE,
    FDEPre2         DOUBLE,
    OpenZTBuy       DOUBLE,
    OpenZAF         DOUBLE,
    VOpenZAF        DOUBLE,
    MA5Value        DOUBLE,
    Wtb             DOUBLE,
    HisHigh         DOUBLE,
    HisLow          DOUBLE,
    MainBusiness    VARCHAR,
    IPO_Price       DOUBLE,
    SafeValue       DOUBLE,
    ShineValue      DOUBLE,
    ShapeValue      DOUBLE
) TIMESTAMP(date) PARTITION BY MONTH
DEDUP UPSERT KEYS(date, code);


-- ------------------------------------------------------------
-- 表名: qd_sector_daily
-- 用途: 板块日级 more_info (~15 字段)
-- 数据源: get_more_info (板块)
-- 时间戳: date ← HqDate
-- 去重: (date, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_sector_daily (
    code            VARCHAR,
    date            TIMESTAMP,
    HqDate          VARCHAR,
    ZAFYesterday    DOUBLE,
    ZAFPre5         DOUBLE,
    ZAFPre10        DOUBLE,
    ZAFPre20        DOUBLE,
    ZTGPNum         DOUBLE,
    fLianB          DOUBLE,
    LastStartZT     VARCHAR,
    EverZTCount     DOUBLE,
    YearZTDay       DOUBLE,
    OpenAmoPre1     DOUBLE,
    CJJEPre1        DOUBLE,
    CJJEPre3        DOUBLE,
    FDEPre1         DOUBLE,
    FDEPre2         DOUBLE
) TIMESTAMP(date) PARTITION BY MONTH
DEDUP UPSERT KEYS(date, code);


-- ------------------------------------------------------------
-- 表名: qd_index_daily
-- 用途: 指数日级 more_info (~10 字段)
-- 数据源: get_more_info (指数)
-- 时间戳: date ← HqDate
-- 去重: (date, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_index_daily (
    code            VARCHAR,
    date            TIMESTAMP,
    HqDate          VARCHAR,
    ZAFYesterday    DOUBLE,
    ZAFPre5         DOUBLE,
    ZAFPre10        DOUBLE,
    ZAFPre20        DOUBLE,
    ZAFPre60        DOUBLE,
    ZAFYear         DOUBLE,
    Zsz             DOUBLE,
    Ltsz            DOUBLE,
    MA5Value        DOUBLE
) TIMESTAMP(date) PARTITION BY MONTH
DEDUP UPSERT KEYS(date, code);
