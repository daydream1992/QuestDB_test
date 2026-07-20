"""k6: 板块联动分析 + 个股角色分类

脚本路径: K:/QuestDB_test/compute/k6_linkage.py
用途: 计算板块联动强度评分 (0-100) + 个股角色分类 (龙头/中军/补涨/跟风)
数据源: qd_sector_flow + qd_sector_snapshot + qd_stock_snapshot + qd_stock_daily + lib.relation_graph
写入表: qd_sector_linkage (60s/轮)
推送: 飞书推送 (满足阈值时推送)
频率: 60s/轮 (由 intraday_loop 60s 块触发)

评分维度:
  - 资金净流入 (40分): >10亿满分, <0亿0分
  - 涨停家数 (25分): >5家满分, =0家0分
  - 连板高度 (20分): 有3板以上满分, 无连板0分 (用昨天的EverZTCount)
  - 龙头表现 (15分): 龙头涨幅>5%满分, <0%0分

个股角色分类:
  - 龙头: 涨幅最高 AND 成交额最大
  - 中军: 涨幅>3% AND 成交额前30%
  - 补涨: 涨幅>0 AND 涨幅<板块平均
  - 跟风: 其他上涨个股
  - 下跌: 不输出
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
from lib.qdb import connect, query_df, executemany_batch, cutoff
from lib.relation_graph import get_sector_stocks, _sector_meta, load_from_json

# 日志
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k6_linkage_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

DST_LINKAGE = 'qd_sector_linkage'
_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')

# 默认配置 (YAML 加载失败时兜底)
# ⚠️ 单位: net_inflow=万元 (Zjl聚合, 见 _deprecated/参考编码用-导出全景快照.py), Ltsz=亿元
_DEFAULT_CFG = {
    'weights': {
        'net_inflow': 0.40, 'zt_count': 0.25, 'ladder_height': 0.20, 'leader_change': 0.15,
    },
    'thresholds': {
        'net_inflow_full': 10000,  # 资金满分门槛: 1亿元 (=10000万元)
        'ladder_full': 3, 'leader_full': 5.0,
    },
    'dynamic': {
        'enabled': True, 'opportunity_pct': 90, 'risk_pct': 15,
        'opportunity_min': 40.0, 'risk_max': 35.0, 'min_sample': 10,
    },
    'fixed': {
        'opportunity_score': 50.0, 'opportunity_leader_change': 5.0,
        'risk_score_low': 25.0, 'risk_outflow': 200000,  # 净流出20亿 (=200000万元)
    },
    'size_tiers': {
        'large':  {'component_min': 500, 'zt_count_full': 10},
        'medium': {'component_min': 100, 'zt_count_full': 5},
        'small':  {'component_min': 0,   'zt_count_full': 3},
    },
}

# 模块级缓存: 避免每轮重新读 YAML (配置变更需重启进程)
_CFG = None


def _load_config() -> dict:
    """从 strategies.yaml 加载 linkage 配置 (带默认兜底)"""
    global _CFG
    if _CFG is not None:
        return _CFG
    try:
        import yaml
        if os.path.exists(_YAML_PATH):
            with open(_YAML_PATH, 'r', encoding='utf-8') as f:
                cfg_all = yaml.safe_load(f) or {}
            lk = cfg_all.get('linkage', {})
            if lk:
                _CFG = {
                    'weights': {**_DEFAULT_CFG['weights'], **(lk.get('weights') or {})},
                    'thresholds': {**_DEFAULT_CFG['thresholds'], **(lk.get('thresholds') or {})},
                    'dynamic': {**_DEFAULT_CFG['dynamic'], **(lk.get('dynamic') or {})},
                    'fixed': {**_DEFAULT_CFG['fixed'], **(lk.get('fixed') or {})},
                    'size_tiers': lk.get('size_tiers') or _DEFAULT_CFG['size_tiers'],
                }
                logger.info('k6_linkage 配置加载自 YAML')
                return _CFG
    except Exception as e:
        logger.warning('k6_linkage 配置加载失败, 用默认值: {}', e)
    _CFG = _DEFAULT_CFG
    return _CFG


def _get_size_tier(component_count: int, cfg: dict) -> tuple:
    """根据成分股数量分档, 返回 (tier_name, zt_count_full)

    大盘/小盘涨停满分门槛不一致: 大盘要求多涨停, 小盘门槛低。
    """
    tiers = cfg.get('size_tiers', _DEFAULT_CFG['size_tiers'])
    if component_count >= tiers.get('large', {}).get('component_min', 500):
        return 'large', tiers.get('large', {}).get('zt_count_full', 10)
    elif component_count >= tiers.get('medium', {}).get('component_min', 100):
        return 'medium', tiers.get('medium', {}).get('zt_count_full', 5)
    else:
        return 'small', tiers.get('small', {}).get('zt_count_full', 3)


def _calc_dynamic_thresholds(scores: dict, cfg: dict) -> tuple:
    """根据全市场评分分布计算动态阈值, 返回 (opportunity_score, risk_score)

    机会阈值 = P90 分位数, 风险阈值 = P15 分位数
    样本不足或关闭动态时回退固定阈值。
    """
    dyn = cfg.get('dynamic', {})
    fixed = cfg.get('fixed', {})

    if not dyn.get('enabled', True):
        return (fixed.get('opportunity_score', 50.0), fixed.get('risk_score_low', 25.0))

    score_vals = list(scores.values())
    if len(score_vals) < dyn.get('min_sample', 10):
        return (fixed.get('opportunity_score', 50.0), fixed.get('risk_score_low', 25.0))

    # 简单分位数计算 (避免 numpy 依赖)
    opp_pct = dyn.get('opportunity_pct', 90)
    risk_pct = dyn.get('risk_pct', 15)
    sorted_scores = sorted(score_vals)
    opp_idx = min(len(sorted_scores) - 1, int(len(sorted_scores) * opp_pct / 100))
    risk_idx = min(len(sorted_scores) - 1, int(len(sorted_scores) * risk_pct / 100))
    opp_threshold = sorted_scores[opp_idx]
    risk_threshold = sorted_scores[risk_idx]

    # 限制范围 (避免极端市场)
    opp_threshold = max(opp_threshold, dyn.get('opportunity_min', 40.0))
    risk_threshold = min(risk_threshold, dyn.get('risk_max', 35.0))

    return (round(opp_threshold, 1), round(risk_threshold, 1))


def _sf(v, default=0.0):
    """安全转换浮点数"""
    try:
        r = float(v)
        if r != r:  # NaN check
            return default
        return r
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════
# 核心计算函数
# ══════════════════════════════════════════════════════════════

def calc_linkage_score(net_inflow, zt_count, ladder_height, leader_change, zt_count_full=5):
    """计算板块联动强度评分 (0-100)

    Args:
        net_inflow: 主力净流入(元)
        zt_count: 涨停家数
        ladder_height: 最高连板数
        leader_change: 龙头涨幅%
        zt_count_full: 涨停满分门槛 (按板块规模分层, 大盘高小盘低)

    Returns:
        float: 评分 0-100
    """
    cfg = _load_config()
    weights = cfg['weights']
    thr = cfg['thresholds']

    score = 0.0

    # 资金分 (权重由 YAML 控制)
    w_net = weights['net_inflow'] * 100
    if net_inflow > 0:
        score += min(w_net, abs(net_inflow) / thr['net_inflow_full'] * w_net)

    # 涨停分 (按板块规模用不同满分门槛)
    w_zt = weights['zt_count'] * 100
    score += min(w_zt, zt_count / zt_count_full * w_zt)

    # 连板分
    w_lb = weights['ladder_height'] * 100
    score += min(w_lb, ladder_height / thr['ladder_full'] * w_lb)

    # 龙头分
    w_lc = weights['leader_change'] * 100
    if leader_change > 0:
        score += min(w_lc, leader_change / thr['leader_full'] * w_lc)

    return round(score, 1)


def classify_stock_role(block_stocks):
    """个股角色分类

    Args:
        block_stocks: {code: {'change': 涨幅%, 'amount': 成交额, 'name': 名称}}

    Returns:
        dict: {龙头: [], 中军: [], 补涨: [], 跟风: []}
    """
    if not block_stocks:
        return {'龙头': [], '中军': [], '补涨': [], '跟风': []}

    # 按成交额排序
    sorted_by_amount = sorted(block_stocks.items(), key=lambda x: -x[1].get('amount', 0))
    top3_amount = [x[0] for x in sorted_by_amount[:3]]

    # 按涨幅排序
    sorted_by_change = sorted(block_stocks.items(), key=lambda x: -x[1].get('change', -999))

    roles = {'龙头': [], '中军': [], '补涨': [], '跟风': []}

    # 计算板块平均涨幅
    changes = [x[1].get('change', 0) for x in block_stocks.items()]
    avg_change = sum(changes) / len(changes) if changes else 0

    # 成交额前30%阈值
    amount_top30_idx = max(1, len(sorted_by_amount) // 3)
    amount_top30_codes = {x[0] for x in sorted_by_amount[:amount_top30_idx]}

    for code, data in block_stocks.items():
        change = data.get('change', 0)
        amount = data.get('amount', 0)
        name = data.get('name', code)

        # 下跌个股不分类
        if change < 0:
            continue

        # 龙头: 涨幅最高 AND 成交额前3
        is_leader = (code == sorted_by_change[0][0]) and (code in top3_amount)

        # 中军: 涨幅>3% AND 成交额前30%
        is_zhongjun = (change > 3) and (code in amount_top30_codes)

        if is_leader:
            roles['龙头'].append(f"{name}({code}) +{change:.2f}%")
        elif is_zhongjun:
            roles['中军'].append(f"{name}({code}) +{change:.2f}%")
        elif change < avg_change:
            roles['补涨'].append(f"{name}({code}) +{change:.2f}%")
        else:
            roles['跟风'].append(f"{name}({code}) +{change:.2f}%")

    return roles


def _get_sector_name(code):
    """取板块名"""
    m = _sector_meta.get(code)
    return m.get('sector_name', code) if m else code


def _get_sector_raw_type(code):
    """获取板块原始分类类型"""
    m = _sector_meta.get(code)
    if not m:
        return '未知'
    sector_type = m.get('sector_type', '')
    if sector_type == 'industry':
        # 进一步区分一/二/三级
        if code.startswith('88'):  # 简化判断，实际需要更精确
            return '行业一级'
        elif code.startswith('89'):
            return '行业二级'
        else:
            return '行业三级'
    elif sector_type == 'concept':
        return '概念板块'
    elif sector_type == 'region':
        return '地区板块'
    elif sector_type == 'style':
        return '风格板块'
    elif sector_type == 'index':
        return '指数'
    return '未知'


# ══════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════

def _load_sector_flow(con, start_time, end_time, limit=100):
    """读板块资金流"""
    sql = f"""
        SELECT code, main_net
        FROM qd_sector_flow
        WHERE flow_time >= '{start_time}' AND flow_time <= '{end_time}'
        ORDER BY main_net DESC
        LIMIT {limit}
    """
    df = query_df(con, sql)
    return {row['code']: {'main_net': row['main_net']}
            for _, row in df.iterrows()} if not df.empty else {}


def _load_sector_snapshot(con, start_time, end_time):
    """读板块快照"""
    sql = f"""
        SELECT code, Outside, Inside
        FROM qd_sector_snapshot
        WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
        LIMIT 200
    """
    df = query_df(con, sql)
    return {row['code']: {'zt_count': row['Outside'], 'dt_count': row['Inside']}
            for _, row in df.iterrows()} if not df.empty else {}


def _load_stock_snapshot(con, start_time, end_time):
    """读个股快照"""
    sql = f"""
        SELECT code, Now, LastClose, Amount
        FROM qd_stock_snapshot
        WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
        AND Amount > 0
        ORDER BY Amount DESC
        LIMIT 5000
    """
    df = query_df(con, sql)
    stocks = {}
    for _, row in df.iterrows():
        code = row['code']
        now = _sf(row['Now'])
        last_close = _sf(row['LastClose'])

        # 计算涨幅
        if last_close > 0:
            change = (now / last_close - 1) * 100
        else:
            change = 0

        # 获取股票名称
        from lib.relation_graph import get_stock_name
        name = get_stock_name(code)

        stocks[code] = {
            'code': code,
            'name': name,
            'change': change,
            'amount': _sf(row['Amount']),
        }
    return stocks


def _load_ladder_data(con, date_str):
    """读连板数据（用昨天的）"""
    sql = f"""
        SELECT code, EverZTCount
        FROM qd_stock_daily
        WHERE date = '{date_str}'
        AND EverZTCount > 0
    """
    df = query_df(con, sql)
    return {row['code']: _sf(row['EverZTCount'])
            for _, row in df.iterrows()} if not df.empty else {}


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def run(con, ctx=None):
    """主入口 (60s/轮)

    Args:
        con: QuestDB connection
        ctx: 上下文对象 (可选，用于传递数据)

    Returns:
        dict: {
            linkage_scores: {block_code: score},
            stock_roles: {block_code: {龙头:[], 中军:[], ...}},
            alerts: {opportunity: [], risk: []},
            calc_duration_ms: int
        }
    """
    start = time.time()
    now_time = cutoff()

    logger.info(f"k6_linkage 开始: {now_time}")

    # 确保 relation_graph 已加载
    if not _sector_meta:
        try:
            load_from_json()
            logger.info("relation_graph 加载完成")
        except Exception as e:
            logger.error('relation_graph 加载失败，终止 k6_linkage: {}', e)
            return {
                'linkage_scores': {},
                'stock_roles': {},
                'alerts': {'opportunity': [], 'risk': []},
                'calc_duration_ms': 0,
            }

    # 使用时间范围查询（最近5分钟）
    start_time = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S')
    end_time = (datetime.now() + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')

    # 昨天日期（用于连板数据）- 修复跨日问题
    today_date = datetime.now().date()
    yesterday = (today_date - timedelta(days=1)).strftime('%Y-%m-%d')

    # 1. 加载数据
    sector_flow = _load_sector_flow(con, start_time, end_time, limit=100)
    sector_snapshot = _load_sector_snapshot(con, start_time, end_time)
    stocks = _load_stock_snapshot(con, start_time, end_time)
    ladder_data = _load_ladder_data(con, yesterday)

    logger.info(f"数据加载: {len(sector_flow)}板块资金流, {len(sector_snapshot)}板块快照, "
                f"{len(stocks)}个股, {len(ladder_data)}连板股")

    # 2. 计算每个板块的联动评分
    cfg = _load_config()
    fixed = cfg['fixed']
    linkage_scores = {}
    stock_roles = {}
    block_details = {}
    block_metrics = {}

    for block_code in sector_flow.keys():
        # 获取板块数据
        flow_data = sector_flow.get(block_code, {})
        snapshot_data = sector_snapshot.get(block_code, {})

        net_inflow = flow_data.get('main_net', 0)
        zt_count = snapshot_data.get('zt_count', 0)

        # 获取成分股
        components = get_sector_stocks(block_code)
        if not components:
            continue

        component_codes = {c['code'] for c in components}
        component_count = len(components)

        # 板块规模分层 → 涨停满分门槛 (大盘/小盘不一致)
        tier_name, zt_count_full = _get_size_tier(component_count, cfg)

        # 筛选板块内的个股
        block_stocks = {code: data for code, data in stocks.items() if code in component_codes}
        if not block_stocks:
            continue

        # 找最高连板
        max_ladder = 0
        for code in component_codes:
            if code in ladder_data:
                max_ladder = max(max_ladder, ladder_data[code])

        # 找龙头（涨幅最大）
        leader_code = max(block_stocks.items(), key=lambda x: x[1].get('change', -999))[0]
        leader_change = block_stocks[leader_code]['change']

        # 计算评分 (按板块规模用不同涨停门槛)
        score = calc_linkage_score(net_inflow, zt_count, max_ladder, leader_change, zt_count_full)
        linkage_scores[block_code] = score

        # 个股角色分类
        roles = classify_stock_role(block_stocks)
        stock_roles[block_code] = roles

        # 存储详细信息
        block_details[block_code] = {
            'ladder_height': max_ladder,
            'leader_code': leader_code,
            'leader_change': leader_change,
        }
        # 板块指标快照（供飞书推送）
        leader_name = block_stocks[leader_code].get('name', leader_code)
        block_metrics[block_code] = {
            'net_inflow': net_inflow,
            'zt_count': zt_count,
            'ladder_height': max_ladder,
            'leader_code': leader_code,
            'leader_name': leader_name,
            'leader_change': leader_change,
            'tier': tier_name,
            'component_count': component_count,
        }

    # 3. 动态阈值: 根据全市场评分分布计算 (避免硬编码)
    opp_threshold, risk_threshold = _calc_dynamic_thresholds(linkage_scores, cfg)
    opp_leader_change = fixed.get('opportunity_leader_change', 5.0)
    risk_outflow = fixed.get('risk_outflow', 2e10)

    logger.info(f"动态阈值: 机会>={opp_threshold} 风险<={risk_threshold} "
                f"(样本{len(linkage_scores)}板块)")

    # 4. 预警检测 (用动态阈值)
    alerts = {'opportunity': [], 'risk': []}
    for block_code, score in linkage_scores.items():
        metrics = block_metrics.get(block_code, {})
        leader_change = metrics.get('leader_change', 0)
        net_inflow = metrics.get('net_inflow', 0)
        leader_code = metrics.get('leader_code', '')
        leader_name = metrics.get('leader_name', block_code)

        # 机会预警: 评分 >= 动态机会阈值 AND 龙头涨幅达标
        if score >= opp_threshold and leader_change >= opp_leader_change:
            alerts['opportunity'].append({
                'block_code': block_code,
                'block_name': _get_sector_name(block_code),
                'score': score,
                'leader_code': leader_code,
                'leader_name': leader_name,
                'leader_change': leader_change,
                'threshold': opp_threshold,
            })

        # 风险预警: 评分 <= 动态风险阈值 OR 净流出超限
        if score <= risk_threshold or net_inflow < -risk_outflow:
            alerts['risk'].append({
                'block_code': block_code,
                'block_name': _get_sector_name(block_code),
                'score': score,
                'net_inflow': net_inflow,
                'threshold': risk_threshold,
            })

    # 5. 写入数据库
    _write_linkage(con, now_time, linkage_scores, stock_roles, sector_flow, sector_snapshot, block_details)

    duration_ms = int((time.time() - start) * 1000)

    logger.info(f"k6_linkage 完成: {len(linkage_scores)}板块, {len(alerts['opportunity'])}机会, "
                f"{len(alerts['risk'])}风险, 耗时{duration_ms}ms")

    return {
        'linkage_scores': linkage_scores,
        'stock_roles': stock_roles,
        'alerts': alerts,
        'block_metrics': block_metrics,
        'thresholds': {'opportunity': opp_threshold, 'risk': risk_threshold},
        'calc_duration_ms': duration_ms,
    }


def _write_linkage(con, now_time, linkage_scores, stock_roles, sector_flow, sector_snapshot, block_details=None):
    """写入板块联动表"""
    if block_details is None:
        block_details = {}

    rows = []
    for block_code, score in linkage_scores.items():
        block_name = _get_sector_name(block_code)
        block_type = _get_sector_raw_type(block_code)

        flow_data = sector_flow.get(block_code, {})
        snapshot_data = sector_snapshot.get(block_code, {})

        net_inflow = flow_data.get('main_net', 0)
        zt_count = snapshot_data.get('zt_count', 0)

        roles = stock_roles.get(block_code, {})

        # 从 block_details 获取龙头和连板信息
        details = block_details.get(block_code, {})
        ladder_height = details.get('ladder_height', 0)
        leader_code = details.get('leader_code', '')
        leader_change = details.get('leader_change', 0)

        row = {
            'linkage_time': now_time,
            'block_code': block_code,
            'block_name': block_name,
            'block_type': block_type,
            'linkage_score': score,
            'net_inflow': net_inflow,
            'zt_count': int(zt_count),
            'ladder_height': int(ladder_height),
            'leader_code': leader_code,
            'leader_change': leader_change,
            'resonance_sectors': '[]',
            'stock_roles': json.dumps(roles, ensure_ascii=False),
            'calc_duration_ms': 0,
        }
        rows.append(row)

    if rows:
        cols = rows[0].keys()
        insert_sql = f"""
            INSERT INTO {DST_LINKAGE} ({', '.join(cols)})
            VALUES ({', '.join(['%s'] * len(cols))})
        """
        executemany_batch(con, insert_sql, rows)


if __name__ == '__main__':
    # 测试运行
    con = connect()
    try:
        result = run(con)
        print(f"计算完成: {len(result['linkage_scores'])} 个板块")
        print(f"机会预警: {len(result['alerts']['opportunity'])}")
        print(f"风险预警: {len(result['alerts']['risk'])}")
    finally:
        con.close()
