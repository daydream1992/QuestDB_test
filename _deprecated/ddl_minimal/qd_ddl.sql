-- ============================================================================
-- QuestDB 测试项目 DDL - 2026-07-02 (简化版: 只取 tq.get_more_info 实际返回的字段)
-- ============================================================================

-- 1. 全市场快照 (来自 101 脚本 88 字段, 50秒/轮, 5000只)
DROP TABLE IF EXISTS qd_snapshots_full;
CREATE TABLE qd_snapshots_full (
    code             VARCHAR,         -- 股票代码
    snapshot_time    TIMESTAMP,       -- 快照时间 (DESIGNATED)
    HqDate           DATE,            -- 行情日期
    stock_type       VARCHAR,         -- stock/sector

    -- 价/成交 (101 脚本中 tq 实际返回)
    ZTPrice          DOUBLE,          -- 涨停价
    DTPrice          DOUBLE,          -- 跌停价
    FzAmo            DOUBLE,          -- 成交金额_万
    ZAF              DOUBLE,          -- 日涨跌幅%
    VOpenZAF         DOUBLE,          -- 抢筹涨幅%
    ZAFYesterday     DOUBLE,
    ZAFPre2D         DOUBLE,
    ZAFPre5          DOUBLE,
    ZAFPre10         DOUBLE,
    ZAFPre20         DOUBLE,
    ZAFPre30         DOUBLE,
    ZAFPre60         DOUBLE,
    ZAFYear          DOUBLE,
    ZAFPreMyMonth    DOUBLE,
    ZAFPreOneYear    DOUBLE,

    -- L2 资金流
    TotalBVol        DOUBLE,          -- 总买量
    TotalSVol        DOUBLE,          -- 总卖量
    FCAmo            DOUBLE,          -- 主买成交额
    BCancel          DOUBLE,
    SCancel          DOUBLE,
    L2TicNum         DOUBLE,
    L2OrderNum       DOUBLE,

    -- 开盘
    OpenZAF          DOUBLE,
    OpenAmo          DOUBLE,
    OpenZTBuy        DOUBLE,
    OpenAmoPre1      DOUBLE,
    OpenVolPre1      DOUBLE,

    -- 成交金额历史
    CJJEPre1         DOUBLE,
    CJJEPre3         DOUBLE,
    FDEPre1          DOUBLE,
    FDEPre2          DOUBLE,

    -- 涨停相关
    fLianB           INT,
    LastStartZT      VARCHAR,
    LastZTHzNum      INT,
    ZTGPNum          INT,
    EverZTCount      INT,
    YearZTDay        INT,
    ConZAFDateNum    INT,

    -- 历史/基础
    HisHigh          DOUBLE,
    HisLow           DOUBLE,
    IPO_Price        DOUBLE,
    MainBusiness     VARCHAR,

    -- 估值
    DynaPE           DOUBLE,
    MorePE           DOUBLE,
    StaticPE_TTM     DOUBLE,
    DYRatio          DOUBLE,
    PB_MRQ           DOUBLE,
    BetaValue        DOUBLE,

    -- 标志
    TPFlag           VARCHAR,
    IsT0Fund         VARCHAR,
    IsZCZGP          VARCHAR,
    IsKzz            VARCHAR,
    Kzz_HSCode       VARCHAR,
    QHMainYYMM       VARCHAR,
    Yield            DOUBLE,
    FreeLtgb         DOUBLE,

    -- 涨速/委比
    vzangsu          DOUBLE,
    Wtb              DOUBLE,

    -- 抓取时间
    fetch_time       TIMESTAMP
) TIMESTAMP(snapshot_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(snapshot_time, code);


-- 2. 1m K 线 (K 线数据从 get_market_snapshot 入, 不在这里算)
DROP TABLE IF EXISTS qd_kline_1m;
CREATE TABLE qd_kline_1m (
    code        VARCHAR,
    kline_time  TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    sum_amount  DOUBLE
) TIMESTAMP(kline_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(kline_time, code);


-- 3. 5m K 线
DROP TABLE IF EXISTS qd_kline_5m;
CREATE TABLE qd_kline_5m (
    code        VARCHAR,
    kline_time  TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    sum_amount  DOUBLE
) TIMESTAMP(kline_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(kline_time, code);


-- 4. 实时快照 (供 K 线合成用, 来自 get_market_snapshot 100 只限制)
DROP TABLE IF EXISTS qd_snapshots_realtime;
CREATE TABLE qd_snapshots_realtime (
    code          VARCHAR,
    snapshot_time TIMESTAMP,
    now           DOUBLE,
    open          DOUBLE,
    high          DOUBLE,
    low           DOUBLE,
    last_close    DOUBLE,
    volume        BIGINT,
    amount        DOUBLE,
    buyp1         DOUBLE,
    sellp1        DOUBLE
) TIMESTAMP(snapshot_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(snapshot_time, code);


-- 5. 技术指标
DROP TABLE IF EXISTS qd_indicators;
CREATE TABLE qd_indicators (
    code            VARCHAR,
    indicator_time  TIMESTAMP,
    close           DOUBLE,
    macd_dif        DOUBLE,
    macd_dea        DOUBLE,
    macd_hist       DOUBLE,
    pressure_high   DOUBLE,
    support_low     DOUBLE,
    boll_upper      DOUBLE,
    boll_mid        DOUBLE,
    boll_lower      DOUBLE
) TIMESTAMP(indicator_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(indicator_time, code);


-- 6. 信号
DROP TABLE IF EXISTS qd_signals;
CREATE TABLE qd_signals (
    signal_time  TIMESTAMP,
    code         VARCHAR,
    signal_type  VARCHAR,
    severity     INT,
    payload      VARCHAR,
    pushed       BOOLEAN
) TIMESTAMP(signal_time) PARTITION BY DAY;


-- 7. 频控
DROP TABLE IF EXISTS qd_signal_log;
CREATE TABLE qd_signal_log (
    code         VARCHAR,
    signal_type  VARCHAR,
    last_push    TIMESTAMP
) TIMESTAMP(last_push) PARTITION BY NONE;


-- 8. 全市场实时快照 (get_market_snapshot 全字段 32 个, 1只/次, 6005只全量)
DROP TABLE IF EXISTS qd_market_snapshot_full;
CREATE TABLE qd_market_snapshot_full (
    code             VARCHAR,         -- 股票代码 (DESIGNATED 用 snapshot_time)
    snapshot_time    TIMESTAMP,       -- 快照时间

    -- 价/成交 (12 字段)
    ItemNum          DOUBLE,
    LastClose        DOUBLE,
    Open             DOUBLE,
    Max              DOUBLE,
    Min              DOUBLE,
    Now              DOUBLE,
    Volume           BIGINT,
    NowVol           BIGINT,
    Amount           DOUBLE,
    Inside           BIGINT,
    Outside          BIGINT,
    Average          DOUBLE,

    -- 笔涨跌/标志 (4 字段)
    TickDiff         DOUBLE,
    InOutFlag        INT,
    Jjjz             DOUBLE,
    XsFlag           INT,

    -- 5档买卖盘 (20 字段, 列表展开)
    Buyp1            DOUBLE,  Buyp2 DOUBLE,  Buyp3 DOUBLE,  Buyp4 DOUBLE,  Buyp5 DOUBLE,
    Buyv1            BIGINT,  Buyv2 BIGINT,  Buyv3 BIGINT,  Buyv4 BIGINT,  Buyv5 BIGINT,
    Sellp1           DOUBLE,  Sellp2 DOUBLE, Sellp3 DOUBLE, Sellp4 DOUBLE, Sellp5 DOUBLE,
    Sellv1           BIGINT,  Sellv2 BIGINT, Sellv3 BIGINT, Sellv4 BIGINT, Sellv5 BIGINT,

    -- 指数/板块 (4 字段)
    UpHome           INT,
    DownHome         INT,
    Before5MinNow    DOUBLE,
    Zangsu           DOUBLE,

    -- 涨幅 (1 字段)
    ZAFPre3          DOUBLE,

    -- 错误标志
    ErrorId          INT
) TIMESTAMP(snapshot_time) PARTITION BY DAY
  DEDUP UPSERT KEYS(snapshot_time, code);
