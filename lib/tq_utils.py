"""tqcenter 工具函数

脚本路径: K:\QuestDB_test\\lib\\tq_utils.py
用途: 股票代码转换 / 标的类型判定 / 全市场代码获取 / 注册表刷新
依赖: tqcenter (lib.tq_client), psycopg2, config.index_codes
数据源: tqcenter get_sector_list + get_stock_list_in_sector + get_stock_list
入库表: qd_code_registry
说明:
  - to_tdx: 转通达信内部格式 "市场编号#代码" (SZ=0, SH=1, BJ=2)
  - route_type: index / sector / stock 三类标的
  - fetch_all_codes: 股票 + 板块 + 指数, 供 refresh_registry 入库
"""

import os
import sys

# 确保项目根目录在 sys.path, 以便 from config.index_codes import ...
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.index_codes import INDEX_CODES, INDEX_CODE_SET  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from lib.qdb import executemany_batch  # noqa: E402

from tqcenter import tq  # noqa: E402  (tqcenter 的 tq 类)

# 市场后缀 → tdx 内部市场编号 (与 tqcenter.MARKET_NUM_BY_SUFFIX 一致)
_MARKET_NUM = {'SZ': '0', 'SH': '1', 'BJ': '2'}

# get_sector_list 的 list_type → 板块分类标签
# 注: tqcenter list_type 默认 0; 这里枚举常见取值, 实际可用范围以 tqcenter 为准
_SECTOR_LIST_TYPES = [
    (0, 'industry'),   # 行业板块
    (1, 'concept'),    # 概念板块
    (2, 'region'),     # 地域板块
    (3, 'style'),      # 风格板块
    (4, 'index'),      # 指数板块
]


def to_tdx(code):
    """股票代码转 tdx 内部格式 "市场编号#代码"

    例:
      '000001.SZ' -> '0#000001'
      '600000.SH' -> '1#600000'
      '873000.BJ' -> '2#873000'

    未知市场后缀原样返回。

    Args:
        code: 标准代码 "代码.市场", 如 '000001.SZ'
    """
    if not code or '.' not in code:
        return code
    code_part, suffix = code.rsplit('.', 1)
    market = _MARKET_NUM.get(suffix.upper())
    if market is None:
        return code
    return '{m}#{c}'.format(m=market, c=code_part)


def route_type(code):
    """判定标的类型: index / sector / stock

    - index:  在 INDEX_CODE_SET 内 (来自 config/index_codes.py)
    - sector: 板块代码 (881xxx 行业 / 880xxx 概念·地域·风格)
    - stock:  其他 (6 位数字 .SZ/.SH/.BJ)

    Args:
        code: 标的代码
    """
    if not code:
        return 'stock'
    if code in INDEX_CODE_SET:
        return 'index'
    if code.startswith('881') or code.startswith('880'):
        return 'sector'
    return 'stock'


def fetch_all_codes():
    """获取全市场代码 (股票 + 板块 + 指数)

    调用 tqcenter:
      - tq.get_stock_list(market='5', list_type=0)  股票
      - tq.get_sector_list(list_type=0..4)          各类板块
      - INDEX_CODE_SET                               指数 (固定列表)

    Returns:
        list[dict]: 每个元素包含
            code             标准代码
            tdx_code         tdx 内部格式 (板块为空)
            name             名称
            code_type        index / sector / stock
            market           SH / SZ / BJ / ''
            sector_category  板块分类 (industry/concept/region/style/index), 仅 sector 有
    """
    init()
    result = []

    # 1. 股票
    # 注: tqcenter get_stock_list 返回 list[str] (标准代码), 兼容 list[dict]
    stocks = safe_call(tq.get_stock_list, market='5', list_type=0) or []
    for s in stocks:
        if isinstance(s, str):
            code, name = s, ''
        else:
            code = s.get('code') or s.get('stock_code') or ''
            name = s.get('name', '') or ''
        if not code:
            continue
        result.append({
            'code': code,
            'tdx_code': to_tdx(code),
            'name': name,
            'code_type': 'stock',
            'market': code.rsplit('.', 1)[-1] if '.' in code else '',
            'sector_category': '',
        })

    # 2. 板块 (枚举 list_type)
    # 注: tqcenter get_sector_list 返回 list[str] (板块代码), 兼容 list[dict]
    seen_sector = set()
    for list_type, category in _SECTOR_LIST_TYPES:
        sectors = safe_call(tq.get_sector_list, list_type=list_type) or []
        for sec in sectors:
            if isinstance(sec, str):
                code, name = sec, ''
            else:
                code = sec.get('block_code', '') or ''
                name = sec.get('block_name', '') or ''
            if not code or code in seen_sector:
                continue
            seen_sector.add(code)
            result.append({
                'code': code,
                'tdx_code': '',  # 板块不走 to_tdx
                'name': name,
                'code_type': 'sector',
                'market': code.rsplit('.', 1)[-1] if '.' in code else '',
                'sector_category': category,
            })

    # 3. 指数 (固定列表, 名称取 INDEX_CODES)
    for idx_code in INDEX_CODE_SET:
        result.append({
            'code': idx_code,
            'tdx_code': to_tdx(idx_code),
            'name': INDEX_CODES.get(idx_code, ''),
            'code_type': 'index',
            'market': idx_code.rsplit('.', 1)[-1] if '.' in idx_code else '',
            'sector_category': '',
        })

    return result


def refresh_registry(con):
    """刷新注册表: 全市场 code upsert 到 qd_code_registry

    qd_code_registry DEDUP UPSERT KEYS(last_seen, code), 重复 code 会幂等更新。
    first_seen / last_seen 均取当前时间 (时序库语义, 查最新用 LATEST ON)。

    Args:
        con: psycopg2 连接 (autocommit=True)

    Returns:
        int: 本次写入的记录数
    """
    from datetime import datetime
    codes = fetch_all_codes()
    now = datetime.now()
    rows = []
    for c in codes:
        rows.append((
            c['code'],
            c.get('tdx_code', ''),
            c.get('name', ''),
            c['code_type'],
            c.get('market', ''),
            now,           # first_seen
            now,           # last_seen
            True,          # is_active
            c.get('sector_category', ''),
        ))
    n = executemany_batch(
        con, 'qd_code_registry',
        ['code', 'tdx_code', 'name', 'code_type', 'market',
         'first_seen', 'last_seen', 'is_active', 'sector_category'],
        rows)
    return n
