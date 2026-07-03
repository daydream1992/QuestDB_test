"""龙虎榜分析

脚本路径: K:\QuestDB_test\\strategy\\lhb_analyzer.py
用途: 解析龙虎榜原始数据, 识别知名游资/机构/北向席位参与情况
依赖: loguru, config.broker_list
数据源: 龙虎榜原始数据 (list[dict]), 每项含 code/name/date/buyers/sellers 席位列表
配置: config/broker_list.py 的 FAMOUS_BROKERS (营业部→身份) / BROKER_LABELS (身份→中文)
说明:
  - 席位 dict 约定字段: operator(营业部名) / buy_amount / sell_amount
  - 匹配 FAMOUS_BROKERS 标注身份: hot_money 游资 / institution 机构 / north 北向
  - 输出每只股票的参与类型集合 + 知名席位明细 + 买方净额
"""

from loguru import logger

from config.broker_list import FAMOUS_BROKERS, BROKER_LABELS


def _classify_operator(operator_name) -> dict:
    """营业部名称 → 身份信息 dict, 未命中返回 None"""
    if not operator_name:
        return None
    broker_id = FAMOUS_BROKERS.get(operator_name)
    if not broker_id:
        return None
    if broker_id.startswith('hot_money'):
        btype = 'hot_money'
    elif broker_id == 'institution':
        btype = 'institution'
    elif broker_id.startswith('north'):
        btype = 'north'
    else:
        btype = 'other'
    return {
        'broker_id': broker_id,
        'label': BROKER_LABELS.get(broker_id, broker_id),
        'type': btype,
    }


def _parse_seats(seats) -> list:
    """解析席位列表, 仅保留知名营业部

    Args:
        seats: list[dict] 含 operator / buy_amount / sell_amount

    Returns:
        list[dict]: {operator, broker_id, label, type,
                     buy_amount, sell_amount, net_amount}
    """
    out = []
    for s in seats or []:
        if not isinstance(s, dict):
            continue
        op = s.get('operator') or s.get('name') or ''
        info = _classify_operator(op)
        if not info:
            continue
        buy = _safe_float(s.get('buy_amount'))
        sell = _safe_float(s.get('sell_amount'))
        out.append({
            'operator': op,
            'broker_id': info['broker_id'],
            'label': info['label'],
            'type': info['type'],
            'buy_amount': buy,
            'sell_amount': sell,
            'net_amount': round(buy - sell, 2),
        })
    return out


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def analyze(lhb_raw) -> list:
    """解析龙虎榜, 识别游资/机构/北向参与

    Args:
        lhb_raw: list[dict] 龙虎榜原始数据, 每项含:
            code: 股票代码
            name: 股票名称
            date: 龙虎榜日期
            buyers: list[dict] 买方席位 (operator/buy_amount/sell_amount)
            sellers: list[dict] 卖方席位

    Returns:
        list[dict]: 每只股票一项:
            {code, name, date, types, famous_seats,
             hotmoney_count, institution_count, north_count, net_buy}
    """
    results = []
    for item in lhb_raw or []:
        if not isinstance(item, dict):
            continue
        code = item.get('code')
        buyers = _parse_seats(item.get('buyers'))
        sellers = _parse_seats(item.get('sellers'))
        all_seats = buyers + sellers
        types = sorted({s['type'] for s in all_seats})
        net_buy = sum(s['net_amount'] for s in buyers)

        results.append({
            'code': code,
            'name': item.get('name', ''),
            'date': item.get('date'),
            'types': types,
            'famous_seats': all_seats,
            'hotmoney_count': sum(1 for s in all_seats if s['type'] == 'hot_money'),
            'institution_count': sum(1 for s in all_seats if s['type'] == 'institution'),
            'north_count': sum(1 for s in all_seats if s['type'] == 'north'),
            'net_buy': round(net_buy, 2),
        })

    logger.info('龙虎榜分析: {} 条, 含游资={}, 机构={}, 北向={}',
                len(results),
                sum(r['hotmoney_count'] for r in results),
                sum(r['institution_count'] for r in results),
                sum(r['north_count'] for r in results))
    return results
