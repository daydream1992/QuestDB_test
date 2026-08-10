"""testv10.2 配置 (settings)

脚本路径: K:/QuestDB_test/testv10.2/settings.py
用途: 双层雷达 + 双引擎 阈值/频率/路径/宇宙 集中配置
说明: 命名 settings 而非 config, 避让项目根 config/ 包 import 冲突 (同 v10.1)
原则: 只放当前真在用的; 未验证字段 (指数表/探照灯字段) 留到 Step 2 探测后补, 不预先堆。
"""

import os
from datetime import time as dtime

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJ_ROOT, 'logs')

# === 交易时段 (生产模式门控, radar_main) ===
TRADING_CLOSE = dtime(15, 0)   # 收盘退出时刻 (盘中无值守时自动停; 先跑一次 close 定格再退)

# === 板块-个股映射 (Layer 0 数据源) ===
# 现有 sector_mapping.parquet (refresh_mapping.py 已生成, 长表 5 列):
#   板块代码/板块名称/行业级别/个股代码/个股名称, 含 一/二/三级 + 概念 (~680 板块)
MAPPING_PARQUET = os.path.join(PROJ_ROOT, 'testv10.2', 'sector_mapping.parquet')
# 雷达中观监控级别 (概念 + 三级 ≈ 398 板块; 可切二级或加一/二级)
MONITOR_LEVELS = {'概念', '三级'}

# === 取数保护 (ticker) ===
CODES_CACHE_TTL = 600          # 全场代码表缓存秒数 (fetch_all_codes)
MOREINFO_TIMEOUT = 8           # 单只 more_info+snapshot 超时 (秒, 防 COM 卡死)
MESO_SCAN_BUDGET = 15.0        # meso 板块扫描聚合预算 (秒, COM 慢时降级保 60s 轮次)
STOCK_DRILL_BUDGET_SEC = 35    # 钻取聚合预算 (秒, 超时 break + 部分降级)
DRILL_TOP_PER_BOARD = 8        # 每 HOT/NEW 板钻取 TopN 成分股 (lean 化, ~20板×8≈160股)
DRILL_TOP_PER_HOT_BOARD = 15   # 涨停≥DRILL_HOT_ZT_THRESH 的热点板块动态扩 TopN (涨停潮覆盖)
DRILL_HOT_ZT_THRESH = 10       # 板块涨停家数 ≥ N 视为热点, TopN 8→15
DRILL_RETRY_BUDGET_SEC = 1.5   # 钻取超时重试预算 (秒; 只重试未完成股, 防关键股静默丢弃)
DRILL_GLOBAL_TOP_N = 20        # 全市场 pct 前 N 补钻 (池外最强票可见, 找最牛股盲区)

# === 推送 (publisher: alert_engine / open_monitor / rotation 等各模块共用) ===
PUSH_TEXT_MAX_PER_MIN = 3   # 事件通道 ≤3 条/分钟 (3桶: 新主线/预警/龙头封板 各1; 龙头封板独立配额防饿死)
WEBHOOK_TIMEOUT = 5         # 飞书 webhook HTTP 超时 (秒)


# === 大盘情绪监控 (sentiment_monitor; 8 维度综合情绪分 → 飞书多维表) ===
# 探针实测全绿 (2026-08-09): 880001.SH单点给涨跌家数+涨跌停家数; 880863.SH持续性;
#                             000688.SH确认科创50指数量级; meso rows复用维度8
SENTIMENT_ENABLE = True             # 总开关
SENTIMENT_INTERVAL_SEC = 60         # 写表频率 (1min/行; 每轮 radar 触发)
SENTIMENT_BITABLE_APP_TOKEN = 'ToVFbNgEqaTjmZs1feZcEVVYnab'  # 已建表固化 (2026-08-09)
SENTIMENT_DRY_RUN = True            # 守红线: 默认 dry-run 只 log, --push 才真写飞书
SENTIMENT_SANITY_ZT_MAX = 6000      # Outside(涨停家数) sanity 上限 (全A≈5500)
SENTIMENT_CAND_DRILL_BUDGET = 2.0   # 连板/封板候选钻取预算 (秒, 超时 break 部分降级)
# 情绪时段聚合 (独立表 v10.2情绪时段, 每段末写 1 行; 保留 240 行 1min 表供飞书分析)
SENTIMENT_SLOTS = [                 # (label, 起始, 结束) — 早/午/后/尾
    ('早盘',  dtime(9, 30), dtime(10, 30)),
    ('午盘',  dtime(10, 30), dtime(11, 30)),
    ('午后',  dtime(13, 0),  dtime(14, 0)),
    ('尾盘',  dtime(14, 0),  dtime(15, 0)),
]
NEAR_LIMIT_CAND_PCT = 9.5           # 连板/封板候选预筛阈值 (df.pct ≥ N)
# 维度7 指数风向: code → (名称, 权重), 权重和=1.0 (参考资料: 沪深300·40/创业·30/科创·20/深成·10)
BROAD_INDICES = {
    '000300.SH': ('沪深300', 0.40),
    '399006.SZ': ('创业板', 0.30),
    '000688.SH': ('科创50', 0.20),
    '399001.SZ': ('深证', 0.10),
}
MARKET_ALL_INDEX = '880001.SH'      # 维度1+2 全A指数 (UpHome/DownHome + Outside/Inside)
YESTERDAY_ZT_INDEX = '880863.SH'    # 维度6 昨日涨停指数 (持续性)

# === 分时段调度 (传导链: 竞价→开盘→盘中→尾盘→收盘) ===
# (stage名, 起始, 结束); radar_main 每轮判 stage, 激活对应计算模块组合
STAGE_RANGES = [
    ('auction',  dtime(9, 15), dtime(9, 25)),    # 竞价: 竞价放量板块+一字龙头
    ('open',     dtime(9, 30), dtime(9, 45)),    # 开盘: 快速拉升(subscribe_hq秒级)
    ('intraday', dtime(9, 45), dtime(11, 30)),   # 盘中上午: 板块轮动+连板梯队
    ('intraday', dtime(13, 0), dtime(14, 0)),    # 盘中下午 (午休 11:30-13:00 落 off, 不跑不推)
    ('tail',     dtime(14, 0), dtime(15, 0)),    # 尾盘: 尾盘拉升+炸板风险
    ('close',    dtime(15, 0), dtime(15, 30)),   # 收盘: 全天总结+次日预判
]


def get_stage(now) -> str:
    """当前交易时段 (auction/open/intraday/tail/close); 非以上返回 'off'。"""
    t = now.time()
    for name, lo, hi in STAGE_RANGES:
        if lo <= t < hi:
            return name
    return 'off'

# === 大盘跳水预警 (L1→L2板块→L3个股 联动警报; 阈值初值, 盘中校准) ===
DIVE_HISTORY_ROUNDS = 6             # history 窗口 (≈5min, 1min/轮)
DIVE_COOLDOWN_SEC = 900             # 跳水冷却 (15min 内只推一次, 防下滑持续连发)
DIVE_FBL_DROP = 25.0                # 封板率 N 个百分点内跌幅 → 触发
DIVE_BLAST_DOUBLE_MIN = 5           # 炸板数翻倍且绝对增量 ≥ N → 触发
DIVE_LOSS_RATIO = 1.5               # 亏钱比突破 N (且较前放大) → 触发
DIVE_SCORE_FROM = 55.0              # 综合分从 ≥ N 跌破 DIVE_SCORE_TO → 触发
DIVE_SCORE_TO = 40.0

# === 板块轮动 (rotation; 传导链②跟风③分化) ===
ROTATION_INTERVAL_SEC = 180        # 轮动榜频率 (3min/行; 比 sentiment 慢, 轮动不需1min)
ROTATION_TOPN = 5                  # 强度榜 Top N
ROTATION_RANK_DELTA = 3            # rank 上升 ≥ N 视为"新晋"(切换事件)
ROTATION_ACCEL = 10.0              # score 单轮涨幅 ≥ N 视为"加速"
ROTATION_LEVELS = {                # (label, table_base, level_filter)
    'concept': ('概念', 'v10.2概念轮动', {'概念'}),
    'sector':  ('行业', 'v10.2行业轮动', {'三级'}),
}
SWITCH_COOLDOWN_SEC = 300        # 高低切卡冷却 (同方向 5min 不重复推)

# === 开盘监控 (open_monitor; 传导链①龙头异动; 9:30-9:45) ===
OPEN_SURGE_THRESHOLD = 5.0          # 相对开盘价涨幅 > N% 触发 (get_market_snapshot Open/Now)
OPEN_MAX_SUB = 100                  # subscribe_hq 同时订阅上限 (文档规定)
OPEN_PROCESS_BATCH = 50             # 主循环单次 process_pending 消化上限 (防过久)
OPEN_POLL_INTERVAL = 15             # 开盘段 run_one_round 降频间隔 (秒; 非开盘段 60s)
# 开盘订阅候选 (占位; 后续接竞价模块产出/昨涨停)。实盘前应换成真实龙头候选源
OPEN_WATCHLIST = [
    '600519.SH', '300750.SZ', '300059.SZ', '688981.SH', '000725.SZ',
    '601318.SH', '002594.SZ', '600036.SH', '688318.SH', '000001.SZ',
]

# === 竞价监控 (auction; 9:15-9:25; 产出放量板块榜 + 一字候选→open_monitor) ===
AUCTION_BOARD_MARKET = '10'         # 竞价板块列表 (get_stock_list market=10 全板块指数)
AUCTION_BOARD_BUDGET = 120.0        # 竞价板块采集预算 (秒; 竞价窗口10min, 余量大)
AUCTION_MAX_ZT_BOARDS = 10          # 竞价涨停板块(Outside>0)取成分股上限
AUCTION_MAX_CANDIDATES = 100        # 一字候选上限 (匹配 open_monitor 订阅上限)
AUCTION_OPENZT_MIN = 0.0            # OpenZTBuy > N 视为一字候选 (竞价涨停买入)
AUCTION_INTERVAL_SEC = 120          # 竞价榜频率 (9:15-9:25 跑 ~5 次)

# === 尾盘监控 (tail; 14:00-15:00 尾盘炸板风险) ===
TAIL_INTERVAL_SEC = 60              # 尾盘炸板检测频率 (1min/行)

# === 盲区补盲 (blindspot; 盘中/tail, 60s 轮询盲区内 subscribe 感知 TopN 状态变更) ===
BLINDSPOT_TOPN = 20             # 盲区订阅 Top N (远低于 subscribe≤100 上限)
BLINDSPOT_SORT = ('ZAF', 0.30, 'FCAmo', 0.25)   # TopN 排序权重 (涨幅+封单, 补盲优先盘口活跃)

# === 机会事件 (opportunity_engine; 3 正: 新主线/趋势确认/龙头封板) ===
OPP_STREAK_ROUNDS = 3           # 板块连续 HOT 达 N 轮 → 趋势确认 (非单轮脉冲)
OPP_LIMIT_ZAF = 9.0             # 个股 ZAF≥N 视为涨停封板候选 (配合 FCAmo>0)
OPP_NEW_MAX = 2                 # 每轮新主线最多推 N 个 (防刷屏)
OPP_LIMIT_MAX = 2               # 每轮龙头封板最多推 N 只
OPP_NEW_SCORE = 50              # 新主线收紧: 板块动能分≥N 才推 (防 28次/天骚扰)
OPP_NEW_ZT = 2                  # 新主线收紧: 板块涨停家数≥N 才推 (弱板不推)
FADE_BLAST_MUTEX_SEC = 1800     # 衰竭后 30min 内同股炸板不推卡 (防双卡连发)
SUBSCRIBE_BACKOFF_SEC = 300     # subscribe 失败熔断退避 (连续3次失败后 5min 不试, 防风暴)

# === 个股排名 (stock_ranking; 池内综合排名, 打板选股底座) ===
RANKING_INTERVAL_SEC = 120          # 个股榜频率 (2min/行)
LEADERBOARD_INTERVAL_SEC = 180     # 板块梯队表频率 (3min/行)

# === 雷达/tick 字段 (Step 3+ 填, 待 Phase 0 G2-G5 验证) ===
# INDEX_TABLE / *_SNAP_FIELDS / FLEET_QUOTA ...
