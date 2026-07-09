"""tqcenter 工具函数

脚本路径: K:\QuestDB_test\\lib\\tq_utils.py
用途: 股票代码转换 / 标的类型判定 / 全市场代码获取 / 注册表刷新
依赖: tqcenter (lib.tq_client), psycopg2, config.index_codes
数据源: tqcenter get_stock_list(市场参数) + get_sector_list + get_stock_list_in_sector
入库表: qd_code_registry
说明:
  - to_tdx: 转通达信内部格式 "市场编号#代码" (SZ=0, SH=1, BJ=2)
  - classify_code: 按 code_type_map 或降级规则判定 11 种标的类型
  - route_type_to_table: 11 种 → 3 种表路由 (stock/sector/index)
  - load_code_type_map: 从 qd_code_registry 加载类型映射
  - fetch_all_codes: 全市场代码 (个股/板块6类/指数/ETF/可转债/REITs)
  - refresh_registry: 入库 qd_code_registry

分类体系 (11 种):
  - stock:       个股 (market=5)
  - industry_l1: 行业一级 (market=16)
  - industry_l2: 行业二级 (market=17)
  - industry_l3: 行业三级 (market=18)
  - concept:     概念板块 (market=12)
  - style:       风格板块 (market=13)
  - region:      地区板块 (market=14)
  - index:       重点指数 (market=9, 补充 INDEX_CODE_SET)
  - etf:         ETF基金 (market=31)
  - kzz:         可转债 (market=32)
  - reits:       REITs (market=30)
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

# 全市场 API 查询参数 (参考脚本 API_MARKET_MAP + NON_STOCK_API)
# (market, code_type, sector_category, list_type)
# 顺序决定去重优先级: 后到覆盖先到
_COLLECT_MARKETS = [
    # 个股 (list_type=0 返回标准代码)
    ('5',  'stock',       '',            0),
    # 行业先写三级再写二级，让二级覆盖重叠的 36 个板块
    ('18', 'industry_l3', 'industry',    1),
    ('17', 'industry_l2', 'industry',    1),
    ('16', 'industry_l1', 'industry',    1),
    # 概念/风格/地区
    ('12', 'concept',     'concept',     1),
    ('13', 'style',       'style',       1),
    ('14', 'region',      'region',      1),
    # 指数
    ('9',  'index',       '',            1),
    # ETF/可转债/REITs
    ('31', 'etf',         '',            1),
    ('32', 'kzz',         '',            1),
    ('30', 'reits',       '',            1),
]

# 表路由映射: code_type → stock/sector/index (采集脚本路由用)
_ROUTE_TO_TABLE = {
    'stock': 'stock',
    'etf': 'stock',
    'kzz': 'stock',
    'reits': 'stock',
    'sector': 'sector',
    'industry_l1': 'sector',
    'industry_l2': 'sector',
    'industry_l3': 'sector',
    'concept': 'sector',
    'style': 'sector',
    'region': 'sector',
    'index': 'index',
}


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


def classify_code(code, code_type_map=None):
    """判定标的类型 (11 种): stock/sector/index/etf/kzz/reits/industry_l1/...

    优先查 code_type_map (从 qd_code_registry 加载, 最准确);
    其次按规则降级判定。

    Args:
        code: 标的代码
        code_type_map: dict[str, str] 从 load_code_type_map 加载

    Returns:
        str: 类型标签
    """
    if not code:
        return 'stock'
    # 优先查映射
    if code_type_map and code in code_type_map:
        ct = code_type_map[code]
        if ct:  # 非 None/非空字符串
            return ct
    # 降级: 硬编码指数
    if code in INDEX_CODE_SET:
        return 'index'
    # 降级: 板块代码特征
    if code.startswith('881') or code.startswith('880'):
        return 'sector'
    return 'stock'


def route_type_to_table(code_type):
    """code_type → 表路由: stock/etf/kzz/reits → stock

    Args:
        code_type: classify_code 返回的类型

    Returns:
        str: 'stock' / 'sector' / 'index'
    """
    return _ROUTE_TO_TABLE.get(code_type, 'stock')


def load_code_type_map(con):
    """从 qd_code_registry 加载 code_type 映射

    Args:
        con: psycopg2 连接

    Returns:
        dict[str, str]: {code: code_type}
    """
    try:
        from lib.qdb import query_df
        df = query_df(con, "SELECT code, code_type FROM qd_code_registry WHERE is_active = true")
        if df is not None and not df.empty:
            return dict(zip(df['code'], df['code_type']))
    except Exception:
        pass
    return {}


def route_type(code):
    """⚠️ 已废弃: 用 classify_code + route_type_to_table 替代

    保留向后兼容 (被 c2/c3 main() 引用), 新代码用 classify_code()。

    判定标的类型: index / sector / stock

    - index:  在 INDEX_CODE_SET 内 (来自 config/index_codes.py)
    - sector: 板块代码 (881xxx 行业 / 880xxx 概念·地域·风格)
    - stock:  其他 (6 位数字 .SZ/.SH/.BJ)
    """
    if not code:
        return 'stock'
    if code in INDEX_CODE_SET:
        return 'index'
    if code.startswith('881') or code.startswith('880'):
        return 'sector'
    return 'stock'


def fetch_all_codes():
    """获取全市场代码 (个股 + 板块 6 类 + 指数 + ETF + 可转债 + REITs)

    调用 tqcenter:
      - 所有 _COLLECT_MARKETS 中的 market 参数
      - 顺序决定去重: 个股先, 行业三级→二级→一级, 概念/风格/地区, 指数, ETF/可转债/REITs

    Returns:
        list[dict]: 每个元素包含
            code             标准代码
            tdx_code         tdx 内部格式 (板块为空)
            name             名称
            code_type        stock / industry_l1 / concept / index / etf / kzz / ...
            market           SH / SZ / BJ / ''
            sector_category  板块分类 (industry/concept/region/style), 非板块为空
    """
    init()
    seen = set()
    result = []

    for market, code_type, sector_category, list_type in _COLLECT_MARKETS:
        items = safe_call(tq.get_stock_list, market=market, list_type=list_type) or []
        name_key = 'Name' if list_type == 1 else 'name'
        code_key = 'Code' if list_type == 1 else 'code'
        for item in items:
            if isinstance(item, str):
                code, name = item, ''
            else:
                code = item.get(code_key, '')
                name = item.get(name_key, '') or ''
            if not code or code in seen:
                continue
            seen.add(code)
            # 计算 tdx_code (个股才需要)
            tdx = ''
            if code_type == 'stock' and '.' in code:
                tdx = to_tdx(code)
            result.append({
                'code': code,
                'tdx_code': tdx,
                'name': name,
                'code_type': code_type,
                'market': code.rsplit('.', 1)[-1] if '.' in code else '',
                'sector_category': sector_category,
            })

    num_by_type = {}
    for c in result:
        num_by_type[c['code_type']] = num_by_type.get(c['code_type'], 0) + 1
    type_str = ', '.join(f'{k}={v}' for k, v in sorted(num_by_type.items()))
    print('全市场代码加载完成: {} 只 ({})'.format(len(result), type_str))
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
