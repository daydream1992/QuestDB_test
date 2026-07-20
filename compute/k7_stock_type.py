"""k7: 票型分类器 (情绪票 / 趋势票 / 混合)

脚本路径: K:/QuestDB_test/compute/k7_stock_type.py
用途: 基于日内数据给每只票打标签, 区分情绪票(游资炒作)和趋势票(机构主导)
数据源:
  qd_stock_daily    - 流通市值/Beta/股性/估值 (日级, 进程级缓存)
  qd_stock_intraday - 换手率/量比/主力净额/涨速 (每轮实时)
  qd_lhb_detail     - 龙虎榜游资/机构上榜 (按天缓存)
  ctx.linkage_scores - 概念热度 (个股所属概念板块联动评分)
写入: 不直接写库, 返回 {code: type_info} 挂 ctx.stock_types 供 p28 消费
频率: 60s/轮 (由 intraday_loop 60s 块触发, 在 k6_linkage 之后)

分类逻辑 (加权评分, 非硬阈值):
  情绪票特征: 小市值 + 高换手 + 高量比 + 爱涨停 + 高Beta + 游资上榜
  趋势票特征: 大市值 + 中换手 + 平稳量比 + 主力持续流入 + 低Beta + 机构上榜

判定:
  sentiment_score > trend_score + 2 → 情绪票
  trend_score > sentiment_score + 2 → 趋势票
  否则 → 混合

设计原则 (用户要求):
  - 情绪票和趋势票阈值完全不同 (4种信号检测时按此分类用不同阈值)
  - 基于日内数据挖掘
  - 龙虎榜/概念热度作为分类维度
"""

import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
from lib.qdb import connect, query_df, cutoff
from lib.relation_graph import get_stock_sectors, load_from_json

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k7_stock_type_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')

# 默认配置 (YAML 覆盖)
# ⚠️ 单位: Ltsz=亿元, Zjl=万元 (tqcenter 字段单位, 见 _deprecated/参考编码用-导出全景快照.py)
_DEFAULT_CFG = {
    # 情绪票评分维度 (各项加分)
    'sentiment': {
        'ltsz_max': 100,          # 流通市值 < 100亿 → +2 (小盘游资爱) [亿元]
        'ltsz_score': 2,
        'hsl_min': 8.0,           # 换手 > 8% → +2 (高换手)
        'hsl_score': 2,
        'lianb_min': 2.0,         # 量比 > 2 → +2 (突击放量)
        'lianb_score': 2,
        'ever_zt_min': 10,        # 历史涨停 > 10次 → +2 (爱涨停)
        'zt_score': 2,
        'beta_min': 1.3,          # Beta > 1.3 → +1 (高弹性)
        'beta_score': 1,
        'hotmoney_score': 2,      # 龙虎榜游资上榜 → +2
        'volatility_score': 1,    # 涨速剧烈 → +1
    },
    # 趋势票评分维度
    'trend': {
        'ltsz_min': 200,          # 流通市值 > 200亿 → +2 (大盘) [亿元]
        'ltsz_score': 2,
        'hsl_low': 2.0,           # 换手 2-8% → +2 (适中)
        'hsl_high': 8.0,
        'hsl_score': 2,
        'lianb_max': 2.0,         # 量比 < 2 → +1 (平稳)
        'lianb_score': 1,
        'zjl_min': 5000,          # 主力净额 > 5000万 → +2 (机构持续流入) [万元]
        'zjl_score': 2,
        'beta_max': 1.2,          # Beta < 1.2 → +1 (低弹性稳健)
        'beta_score': 1,
        'institution_score': 2,   # 龙虎榜机构上榜 → +2
        'pe_min': 10.0,           # 估值合理 → +1
        'pe_max': 40.0,
        'pe_score': 1,
    },
    # 分类判定
    'classify_gap': 2,           # 分差 > 2 才明确分类, 否则混合
    # 概念热度
    'concept_heat_pct': 70,      # 个股所属概念板块评分 >= P70 视为高热度
}

_CFG = None
# 进程级缓存 (daily/lhb 一天不变, 避免每轮查库)
_DAILY_CACHE = None            # {code: {Ltsz, Beta, EverZT, PE}}
_DAILY_CACHE_DATE = None       # 缓存日期
_LHB_CACHE = None              # {code: {has_hotmoney, has_institution, has_north}}
_LHB_CACHE_DATE = None


def _load_config() -> dict:
    """从 strategies.yaml 加载 stock_type 配置"""
    global _CFG
    if _CFG is not None:
        return _CFG
    try:
        import yaml
        if os.path.exists(_YAML_PATH):
            with open(_YAML_PATH, 'r', encoding='utf-8') as f:
                cfg_all = yaml.safe_load(f) or {}
            st = cfg_all.get('stock_type', {})
            if st:
                _CFG = {
                    'sentiment': {**_DEFAULT_CFG['sentiment'], **(st.get('sentiment') or {})},
                    'trend': {**_DEFAULT_CFG['trend'], **(st.get('trend') or {})},
                    'classify_gap': st.get('classify_gap', _DEFAULT_CFG['classify_gap']),
                    'concept_heat_pct': st.get('concept_heat_pct', _DEFAULT_CFG['concept_heat_pct']),
                }
                return _CFG
    except Exception as e:
        logger.warning('k7 配置加载失败, 用默认值: {}', e)
    _CFG = _DEFAULT_CFG
    return _CFG


def _sf(v, default=0.0):
    """安全转 float"""
    try:
        r = float(v)
        if r != r:
            return default
        return r
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════
# 数据加载 (带缓存)
# ══════════════════════════════════════════════════════════════

def _load_daily_features(con):
    """加载日级特征 (市值/Beta/股性/估值), 进程级缓存"""
    global _DAILY_CACHE, _DAILY_CACHE_DATE
    today = datetime.now().strftime('%Y-%m-%d')
    if _DAILY_CACHE is not None and _DAILY_CACHE_DATE == today:
        return _DAILY_CACHE

    # 取最近有数据的一天
    df = query_df(con, """
        SELECT code, Ltsz, BetaValue, EverZTCount, YearZTDay, StaticPE_TTM
        FROM qd_stock_daily
        WHERE date = (SELECT MAX(date) FROM qd_stock_daily)
    """)
    cache = {}
    if not df.empty:
        for _, r in df.iterrows():
            cache[r['code']] = {
                'ltsz': _sf(r['Ltsz']),
                'beta': _sf(r['BetaValue']),
                'ever_zt': _sf(r['EverZTCount']),
                'year_zt': _sf(r['YearZTDay']),
                'pe': _sf(r['StaticPE_TTM']),
            }
    _DAILY_CACHE = cache
    _DAILY_CACHE_DATE = today
    logger.info('k7 日级特征加载: {} 只', len(cache))
    return cache


def _load_lhb_features(con):
    """加载龙虎榜特征 (近5日是否上龙虎榜 + 营业部类型), 按天缓存"""
    global _LHB_CACHE, _LHB_CACHE_DATE
    today = datetime.now().strftime('%Y-%m-%d')
    if _LHB_CACHE is not None and _LHB_CACHE_DATE == today:
        return _LHB_CACHE

    # 近5天龙虎榜 (broker_type: institution/north_*/hot_money; 空串=未识别, 含游资嫌疑)
    # QuestDB dateadd 不接受子查询参数, 分两步查
    max_df = query_df(con, 'SELECT MAX(lhb_date) mx FROM qd_lhb_detail')
    if max_df.empty or max_df['mx'].iloc[0] is None:
        _LHB_CACHE = {}
        _LHB_CACHE_DATE = today
        return {}
    max_date = pd.Timestamp(max_df['mx'].iloc[0])
    cutoff_date = (max_date - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
    df = query_df(con, f"""
        SELECT code, broker_type, MAX(lhb_date) last_date
        FROM qd_lhb_detail
        WHERE lhb_date >= '{cutoff_date}'
        GROUP BY code, broker_type
    """)
    cache = {}
    if not df.empty:
        for _, r in df.iterrows():
            code = r['code']
            bt = str(r['broker_type'] or '')
            if code not in cache:
                cache[code] = {'has_hotmoney': False, 'has_institution': False, 'has_north': False, 'has_lhb': True}
            # 游资: hotmoney 或 未识别(空串, 龙虎榜常客多为游资)
            if bt in ('hot_money', '', 'hotmoney'):
                cache[code]['has_hotmoney'] = True
            if bt in ('institution',):
                cache[code]['has_institution'] = True
            if bt.startswith('north'):
                cache[code]['has_north'] = True
    _LHB_CACHE = cache
    _LHB_CACHE_DATE = today
    logger.info('k7 龙虎榜特征加载: {} 只上榜', len(cache))
    return cache


def _load_intraday_features(con, codes):
    """加载实时日内特征 (换手/量比/主力/涨速), 每轮实时"""
    if not codes:
        return {}
    # 5分钟时间窗 (与 k6 一致)
    end_time = (datetime.now() + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')
    start_time = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S')
    time_cond = f"snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'"
    # code 来源 relation_graph (受控), 仍转义单引号防注入
    if len(codes) < 500:
        safe_codes = [str(c).replace("'", "''") for c in codes if c]
        codes_str = ','.join([f"'{c}'" for c in safe_codes])
        sql = f"SELECT code, fHSL, fLianB, Zjl, vzangsu FROM qd_stock_intraday WHERE {time_cond} AND code IN ({codes_str})"
    else:
        sql = f"SELECT code, fHSL, fLianB, Zjl, vzangsu FROM qd_stock_intraday WHERE {time_cond}"
    df = query_df(con, sql)
    # 取每 code 最新一行
    if df.empty:
        return {}
    df = df.sort_values('snapshot_time').groupby('code', as_index=False).last()
    return {r['code']: {
        'hsl': _sf(r['fHSL']),
        'lianb': _sf(r['fLianB']),
        'zjl': _sf(r['Zjl']),
        'zangsu': _sf(r['vzangsu']),
    } for _, r in df.iterrows()}


# ══════════════════════════════════════════════════════════════
# 分类核心
# ══════════════════════════════════════════════════════════════

def classify_stock_type(code, daily, intraday, lhb, cfg):
    """对单只票分类

    Returns:
        dict: {
            type: 'sentiment'|'trend'|'mixed',
            sentiment_score: float,
            trend_score: float,
            reasons: [str],  # 命中的特征
        }
    """
    s_cfg = cfg['sentiment']
    t_cfg = cfg['trend']

    s_score = 0.0
    t_score = 0.0
    reasons = []

    d = daily or {}
    i = intraday or {}
    l = lhb or {}

    ltsz = d.get('ltsz', 0)
    beta = d.get('beta', 0)
    ever_zt = d.get('ever_zt', 0)
    pe = d.get('pe', 0)
    hsl = i.get('hsl', 0)
    lianb = i.get('lianb', 0)
    zjl = i.get('zjl', 0)
    zangsu = i.get('zangsu', 0)

    # ---- 情绪票特征 ----
    if 0 < ltsz < s_cfg['ltsz_max']:
        s_score += s_cfg['ltsz_score']
        reasons.append(f'小市值{ltsz/1e8:.0f}亿')
    if hsl > s_cfg['hsl_min']:
        s_score += s_cfg['hsl_score']
        reasons.append(f'高换手{hsl:.1f}%')
    if lianb > s_cfg['lianb_min']:
        s_score += s_cfg['lianb_score']
        reasons.append(f'高量比{lianb:.1f}')
    if ever_zt > s_cfg['ever_zt_min']:
        s_score += s_cfg['zt_score']
        reasons.append(f'爱涨停{ever_zt:.0f}次')
    if beta > s_cfg['beta_min']:
        s_score += s_cfg['beta_score']
    if l.get('has_hotmoney'):
        s_score += s_cfg['hotmoney_score']
        reasons.append('游资上榜')
    if abs(zangsu) > 0.5:
        s_score += s_cfg['volatility_score']

    # ---- 趋势票特征 ----
    if ltsz > t_cfg['ltsz_min']:
        t_score += t_cfg['ltsz_score']
        reasons.append(f'大市值{ltsz/1e8:.0f}亿')
    if t_cfg['hsl_low'] <= hsl <= t_cfg['hsl_high']:
        t_score += t_cfg['hsl_score']
    if 0 < lianb < t_cfg['lianb_max']:
        t_score += t_cfg['lianb_score']
    if zjl > t_cfg['zjl_min']:
        t_score += t_cfg['zjl_score']
        reasons.append(f'主力流入{zjl/1e8:.2f}亿')
    if 0 < beta < t_cfg['beta_max']:
        t_score += t_cfg['beta_score']
    if l.get('has_institution'):
        t_score += t_cfg['institution_score']
        reasons.append('机构上榜')
    if t_cfg['pe_min'] <= pe <= t_cfg['pe_max']:
        t_score += t_cfg['pe_score']

    # ---- 判定 ----
    gap = cfg['classify_gap']
    if s_score > t_score + gap:
        stock_type = 'sentiment'
    elif t_score > s_score + gap:
        stock_type = 'trend'
    else:
        stock_type = 'mixed'

    return {
        'type': stock_type,
        'sentiment_score': s_score,
        'trend_score': t_score,
        'reasons': reasons,
    }


def calc_concept_heat(code, linkage_scores):
    """计算个股概念热度 = 所属概念板块中的最高联动评分

    Returns:
        float: 最高概念联动评分 (0 表示不在任何有评分的强势板块)
    """
    sectors = get_stock_sectors(code)
    if not sectors:
        return 0.0
    max_score = 0.0
    for s in sectors:
        bc = s.get('block_code', '')
        # 只看概念/行业板块的联动评分
        if bc in linkage_scores:
            max_score = max(max_score, linkage_scores[bc])
    return max_score


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def run(con, ctx=None):
    """主入口 (60s/轮, 在 k6_linkage 之后)

    只对「强势板块成分股」分类 (板块聚焦, 避免全市场扫描)

    Returns:
        dict: {
            code: {type, sentiment_score, trend_score, reasons, concept_heat}
        }
    """
    start = time.time()
    cfg = _load_config()

    # 取强势板块 (k6_linkage 产出)
    linkage_scores = getattr(ctx, 'linkage_scores', {}) if ctx else {}
    if not linkage_scores:
        logger.debug('k7 无板块联动数据, 跳过')
        return {}

    # 动态机会阈值: 只对强势板块成分股分类
    thresholds = getattr(ctx, 'linkage_thresholds', {}) if ctx else {}
    opp_threshold = thresholds.get('opportunity', 0) if thresholds else 0
    if opp_threshold == 0:
        # 兜底: 取所有评分 > 0 的板块 Top20
        strong_blocks = sorted(linkage_scores.items(), key=lambda x: -x[1])[:20]
    else:
        strong_blocks = [(bc, s) for bc, s in linkage_scores.items() if s >= opp_threshold * 0.6]
        if not strong_blocks:
            strong_blocks = sorted(linkage_scores.items(), key=lambda x: -x[1])[:20]

    # 收集成分股 (去重)
    target_codes = set()
    for block_code, _ in strong_blocks:
        from lib.relation_graph import get_sector_stocks
        stocks = get_sector_stocks(block_code)
        for s in stocks:
            target_codes.add(s.get('code', ''))

    # 确保 relation_graph 已加载 (概念热度依赖它)
    try:
        from lib.relation_graph import _sector_meta
        if not _sector_meta:
            load_from_json()
    except Exception as e:
        logger.warning('k7 relation_graph 加载失败, 概念热度将为0: {}', e)

    logger.info(f'k7 待分类: {len(target_codes)} 只 (来自 {len(strong_blocks)} 强势板块)')

    # 加载特征
    daily_feat = _load_daily_features(con)
    lhb_feat = _load_lhb_features(con)
    intraday_feat = _load_intraday_features(con, list(target_codes))

    # 分类
    result = {}
    for code in target_codes:
        if not code:
            continue
        cls = classify_stock_type(
            code,
            daily_feat.get(code),
            intraday_feat.get(code),
            lhb_feat.get(code),
            cfg,
        )
        cls['concept_heat'] = calc_concept_heat(code, linkage_scores)
        result[code] = cls

    duration_ms = int((time.time() - start) * 1000)
    # 统计
    n_sent = sum(1 for c in result.values() if c['type'] == 'sentiment')
    n_trend = sum(1 for c in result.values() if c['type'] == 'trend')
    n_mixed = sum(1 for c in result.values() if c['type'] == 'mixed')
    logger.info(f'k7 完成: {len(result)}只 (情绪{n_sent}/趋势{n_trend}/混合{n_mixed}), 耗时{duration_ms}ms')

    return result


if __name__ == '__main__':
    # 测试: 用模拟 ctx 跑分类
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    class _CtxStub:
        pass

    con = connect()
    try:
        # 先跑 k6 拿 linkage_scores
        from compute.k6_linkage import run as k6_run
        k6_result = k6_run(con)
        ctx = _CtxStub()
        ctx.linkage_scores = k6_result.get('linkage_scores', {})
        ctx.linkage_thresholds = k6_result.get('thresholds', {})

        result = run(con, ctx)

        # 按 type 统计输出样例
        for t in ('sentiment', 'trend', 'mixed'):
            samples = [(c, v) for c, v in result.items() if v['type'] == t][:5]
            print(f'\n=== {t} ({len([1 for v in result.values() if v["type"]==t])}只) ===')
            for code, v in samples:
                print(f'  {code}: s={v["sentiment_score"]:.0f} t={v["trend_score"]:.0f} '
                      f'热度={v["concept_heat"]:.0f} | {", ".join(v["reasons"][:3])}')
    finally:
        con.close()
