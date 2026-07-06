-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\06_signals.sql
-- 用途: 策略信号 + 频控日志 + 决策 + 持仓 + 策略评估
-- 数据源: 由 strategy 模块生成
-- 时间戳: 各表的 *_time 字段
-- 去重: DEDUP UPSERT KEYS(*_time, code/strategy_name)
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_signals
-- 用途: 信号事件 (每次策略命中产生一条)
-- 数据源: strategy/plugins/* 各策略插件
-- 时间戳: signal_time (信号触发时刻)
-- 字段映射:
--   code          ← 触发信号的标的代码
--   signal_time   ← datetime.now() (信号触发时刻)
--   strategy_name ← 策略标识 (如 zt_daban / macd_golden_vol)
--   signal_type   ← 信号类型 (buy/sell/warn/observe)
--   signal_score  ← 信号评分 (0-100, 越高越强)
--   price         ← 触发时现价
--   volume        ← 触发时成交量
--   reason        ← 信号原因 (人类可读描述)
--   metadata      ← 附加数据 (JSON 字符串, 存放策略特有参数)
-- 去重: (signal_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_signals (
    code           VARCHAR,
    signal_time    TIMESTAMP,
    strategy_name  VARCHAR,
    signal_type    VARCHAR,
    signal_score   DOUBLE,
    price          DOUBLE,
    volume         BIGINT,
    reason         VARCHAR,
    metadata       VARCHAR
) TIMESTAMP(signal_time) PARTITION BY DAY
DEDUP UPSERT KEYS(signal_time, code);


-- ------------------------------------------------------------
-- 表名: qd_signal_log
-- 用途: 信号推送频控日志 (防止飞书刷屏)
-- 数据源: runner 推送模块
-- 时间戳: log_time (日志记录时刻)
-- 字段映射:
--   log_time        ← datetime.now()
--   strategy_name   ← 策略标识
--   signal_count    ← 当前窗口内信号累计次数
--   last_push_time  ← 上次推送时间
--   cooldown_sec    ← 冷却时间 (秒)
--   pushed          ← 本次是否实际推送
-- 去重: (log_time, strategy_name)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_signal_log (
    log_time        TIMESTAMP,
    strategy_name   VARCHAR,
    signal_count    INT,
    last_push_time  TIMESTAMP,
    cooldown_sec    INT,
    pushed          BOOLEAN
) TIMESTAMP(log_time) PARTITION BY DAY
DEDUP UPSERT KEYS(log_time, strategy_name);


-- ------------------------------------------------------------
-- 表名: qd_decisions
-- 用途: 策略决策 (买/卖/持/止损/止盈)
-- 数据源: runner 决策引擎 (汇总多策略信号后产出)
-- 时间戳: decision_time (决策时刻)
-- 字段映射:
--   decision_time  ← datetime.now()
--   code           ← 标的代码
--   strategy_name  ← 触发决策的策略
--   action         ← 决策动作 (buy/sell/hold/stop_loss/stop_profit)
--   position_size  ← 建议仓位 (%)
--   price          ← 决策时现价
--   reason         ← 决策原因
-- 去重: (decision_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_decisions (
    decision_time   TIMESTAMP,
    code            VARCHAR,
    strategy_name   VARCHAR,
    action          VARCHAR,
    position_size   DOUBLE,
    price           DOUBLE,
    reason          VARCHAR
) TIMESTAMP(decision_time) PARTITION BY DAY
DEDUP UPSERT KEYS(decision_time, code);


-- ------------------------------------------------------------
-- 表名: qd_positions
-- 用途: 持仓跟踪 (模拟盘/实盘持仓状态)
-- 数据源: runner 持仓管理
-- 时间戳: update_time (持仓更新时刻)
-- 字段映射:
--   update_time       ← datetime.now()
--   code              ← 标的代码
--   direction         ← 方向 (long/short)
--   entry_price       ← 开仓价
--   current_price     ← 当前价
--   quantity          ← 持仓数量
--   pnl               ← 浮动盈亏 (元)
--   pnl_pct           ← 浮动盈亏 (%)
--   stop_loss_price   ← 止损价
--   take_profit_price ← 止盈价
--   status            ← 状态 (open/closed)
-- 去重: (update_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_positions (
    update_time       TIMESTAMP,
    code              VARCHAR,
    direction         VARCHAR,
    entry_price       DOUBLE,
    current_price     DOUBLE,
    quantity          BIGINT,
    pnl               DOUBLE,
    pnl_pct           DOUBLE,
    stop_loss_price   DOUBLE,
    take_profit_price DOUBLE,
    status            VARCHAR
) TIMESTAMP(update_time) PARTITION BY DAY
DEDUP UPSERT KEYS(update_time, code);


-- ------------------------------------------------------------
-- 表名: qd_strategy_eval
-- 用途: 策略评估 (每日/每周统计策略表现)
-- 数据源: backtest/runner 评估模块
-- 时间戳: eval_time (评估时刻)
-- 字段映射:
--   eval_time      ← datetime.now()
--   strategy_name  ← 策略标识
--   total_signals  ← 统计期内总信号数
--   win_count      ← 盈利次数
--   loss_count     ← 亏损次数
--   win_rate       ← 胜率 (%)
--   total_pnl      ← 累计盈亏 (元)
--   profit_factor  ← 盈亏比 (总盈利/总亏损)
--   max_drawdown   ← 最大回撤 (%)
-- 去重: (eval_time, strategy_name)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_strategy_eval (
    eval_time       TIMESTAMP,
    strategy_name   VARCHAR,
    total_signals   INT,
    win_count       INT,
    loss_count      INT,
    win_rate        DOUBLE,
    total_pnl       DOUBLE,
    profit_factor   DOUBLE,
    max_drawdown    DOUBLE
) TIMESTAMP(eval_time) PARTITION BY MONTH
DEDUP UPSERT KEYS(eval_time, strategy_name);
