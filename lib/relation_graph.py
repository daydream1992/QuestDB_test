"""6 维关系图谱

脚本路径: K:/QuestDB_test/lib/relation_graph.py
用途: 加载 / 查询 / 同步 板块-个股映射 (行业·概念·地域·风格·指数) + 个股行业三级分类
依赖: psycopg2, lib.qdb
数据源: K:/QuestDB_test/data/market_data/市场数据/
入库表:
  - qd_sector_meta          板块元数据
  - qd_stock_industry       个股申万三级分类
  - qd_map_concept_stock    概念板块-个股
  - qd_map_region_stock     地域板块-个股
  - qd_map_style_stock      风格板块-个股
  - qd_map_index_stock      指数-成份股
说明:
  - 新版 JSON 格式: 板块索引.json + 名称映射.json + 子目录下的板块列表/成分股
  - 内存维护正向 (板块->个股) 与反向 (个股->板块) 索引
  - 行业板块-个股直接映射无独立表 (通过 qd_stock_industry 三级分类体现)
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# JSON 默认目录
from config.paths import MARKET_DATA_JSON_DIR as DEFAULT_JSON_DIR

# ---------------- 内存映射 ----------------
# 板块元数据: {block_code: {sector_name, sector_type, stock_count}}
_sector_meta = {}
# 个股行业三级分类: {stock_code: {industry_l1, industry_l2, industry_l3,
#                       industry_l1_code, industry_l2_code, industry_l3_code}}
_stock_industry = {}
# 板块 -> 个股 正向映射: {block_code: [{code, name}, ...]}
_map_industry_stock = {}
_map_concept_stock = {}
_map_region_stock = {}
_map_style_stock = {}
_map_index_stock = {}
# 个股 -> 板块 反向索引: {stock_code: [{block_code, block_name, sector_type}, ...]}
_stock_to_sectors = {}
# 板块原始分类类型: {block_code: '行业一级' | '行业二级' | '行业三级' | '概念板块' | ...}
_sector_raw_type = {}
# 名称映射缓存: {stock_code: {name, type}}
_name_data = {}

# 新版 JSON 目录映射
_SUB_DIRS = {
    '行业': '行业',
    '概念': '概念',
    '地区': '地区',
    '风格': '风格',
    '指数': '指数',
}
# 板块索引 category.type → sector_type
_CATEGORY_TYPE_MAP = {
    '行业一级': 'industry',
    '行业二级': 'industry',
    '行业三级': 'industry',
    '概念板块': 'concept',
    '地区板块': 'region',
    '风格板块': 'style',
    '指数': 'index',
}


def _load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.warning('JSON 解析失败 {}: {}', path, e)
        return {}


def _flatten_industry_tree(nodes):
    """递归展开行业层级树, 返回 [{code, name}, ...]"""
    result = []
    for node in nodes:
        result.append({'code': node['code'], 'name': node['name']})
        if 'children' in node and node['children']:
            result.extend(_flatten_industry_tree(node['children']))
    return result


def load_from_json(json_dir=None):
    """从新版 JSON 加载映射到内存

    新版 JSON 格式 (data/market_data/市场数据/):
      - 板块索引.json         {block_code: {name, categories: [{type, category}]}}
      - 个股板块全景.json      {stock_code: [{code, name, type}, ...]}
      - 名称映射.json          {stock_code: {name, type}}
      - 行业/股票行业映射.json  {stock_code: {level1: {code, name}, level2: ..., level3}}
      - 概念/概念成分股.json    {block_code: [stock_codes]}
      - 各地区下方都是 dict of lists

    Args:
        json_dir: JSON 目录, 默认 K:\\QuestDB_test\\data\\market_data\\市场数据
    """
    json_dir = json_dir or DEFAULT_JSON_DIR

    # 清空内存
    _sector_meta.clear()
    _stock_industry.clear()
    _map_industry_stock.clear()
    _map_concept_stock.clear()
    _map_region_stock.clear()
    _map_style_stock.clear()
    _map_index_stock.clear()
    _stock_to_sectors.clear()
    _sector_raw_type.clear()

    # 1. 板块索引 → _sector_meta (来源统一)
    #    板块索引.json 包含全部板块的名称和分类标签
    idx_path = os.path.join(json_dir, '板块索引.json')
    idx_data = _load_json(idx_path)
    for code, info in idx_data.items():
        # sector_type: 取第一个可识别的分类
        sector_type = 'index'  # fallback
        raw_cat_type = ''  # 原始分类类型
        for cat in info.get('categories', []):
            cat_t = cat.get('type', '')
            if cat_t in _CATEGORY_TYPE_MAP:
                sector_type = _CATEGORY_TYPE_MAP[cat_t]
                raw_cat_type = cat_t
                break
        _sector_meta[code] = {
            'sector_name': info.get('name', ''),
            'sector_type': sector_type,
            'stock_count': 0,
        }
        if raw_cat_type:
            _sector_raw_type[code] = raw_cat_type
    logger.info('板块元数据: %d 个', len(_sector_meta))

    # 2. 名称映射 → 股票名称查表 (概念/地区/风格/指数的成分股只有 code 无 name)
    name_path = os.path.join(json_dir, '名称映射.json')
    name_data = _load_json(name_path)
    # 写入模块级缓存, 供 get_stock_name() 查询
    _name_data.clear()
    _name_data.update(name_data)

    # 3. 概念/地区/风格/指数 板块-个股映射 (dict of lists)
    _load_new_sector_stocks(json_dir, '概念/概念成分股.json', _map_concept_stock, 'concept', name_data)
    _load_new_sector_stocks(json_dir, '地区/地区成分股.json', _map_region_stock, 'region', name_data)
    _load_new_sector_stocks(json_dir, '风格/风格成分股.json', _map_style_stock, 'style', name_data)
    _load_new_sector_stocks(json_dir, '指数/指数成分股.json', _map_index_stock, 'index', name_data)

    # 4. 行业板块-个股映射 (从股票行业映射.json 反向推导)
    _load_industry_mapping(json_dir, name_data)

    # 5. 个股板块全景 → 补全反向索引
    _load_full_reverse_index(json_dir)

    logger.info('关系图谱加载完成: 板块=%d, 个股行业=%d, 反向索引=%d',
                len(_sector_meta), len(_stock_industry), len(_stock_to_sectors))


def _load_new_sector_stocks(json_dir, rel_path, target_map, sector_type, name_data):
    """加载新版板块-个股映射 (dict of lists), 同时回填 stock_count 与反向索引

    新格式: {block_code: [stock_code, ...]}
    """
    path = os.path.join(json_dir, rel_path)
    data = _load_json(path)
    if not data:
        logger.warning('文件为空或不存在: %s', rel_path)
        return
    for code, stock_codes in data.items():
        stocks = [{'code': sc, 'name': _lookup_name(sc, name_data)}
                  for sc in (stock_codes or [])]
        target_map[code] = stocks
        # 回填板块元数据
        if code in _sector_meta:
            _sector_meta[code]['stock_count'] = len(stocks)
        # 反向索引
        for s in stocks:
            sc = s['code']
            if sc:
                _stock_to_sectors.setdefault(sc, []).append({
                    'block_code': code,
                    'block_name': _sector_meta.get(code, {}).get('sector_name', ''),
                    'sector_type': sector_type,
                })


def _lookup_name(stock_code, name_data):
    """查股票名称, 找不到返回空串"""
    info = name_data.get(stock_code, {})
    return info.get('name', '') if info else ''


def _load_industry_mapping(json_dir, name_data):
    """加载行业板块-个股映射 + 个股行业三级分类

    新版 JSON 用 股票行业映射.json 表达每个股票的行业三级分类,
    需要反向推导行业板块→个股的关系。
    """
    path = os.path.join(json_dir, '行业/股票行业映射.json')
    data = _load_json(path)
    if not data:
        logger.warning('行业映射文件为空或不存在')
        return

    # 行业板块索引: {block_code -> {name, stock_codes}}
    industry_blocks = {}

    for stock_code, levels in data.items():
        for level_key in ('level1', 'level2', 'level3'):
            lv = levels.get(level_key, {})
            lv_code = lv.get('code', '') if isinstance(lv, dict) else ''
            lv_name = lv.get('name', '') if isinstance(lv, dict) else ''
            if not lv_code:
                continue
            if lv_code not in industry_blocks:
                industry_blocks[lv_code] = {'name': lv_name, 'codes': set()}
            industry_blocks[lv_code]['codes'].add(stock_code)

        # 个股行业三级分类 (原字段名保持兼容)
        l1 = levels.get('level1', {})
        l2 = levels.get('level2', {})
        l3 = levels.get('level3', {})
        if isinstance(l1, dict) and isinstance(l2, dict) and isinstance(l3, dict):
            _stock_industry[stock_code] = {
                'industry_l1': l1.get('name', ''),
                'industry_l2': l2.get('name', ''),
                'industry_l3': l3.get('name', ''),
                'industry_l1_code': l1.get('code', ''),
                'industry_l2_code': l2.get('code', ''),
                'industry_l3_code': l3.get('code', ''),
            }

    # 写入行业板块→个股映射 + 回填 _sector_meta
    for code, blk in industry_blocks.items():
        stock_codes = blk['codes']
        stocks = [{'code': sc, 'name': _lookup_name(sc, name_data)}
                  for sc in sorted(stock_codes)]
        _map_industry_stock[code] = stocks
        if code not in _sector_meta:
            _sector_meta[code] = {
                'sector_name': blk['name'],
                'sector_type': 'industry',
                'stock_count': len(stocks),
            }
        else:
            _sector_meta[code]['stock_count'] = len(stocks)


def _load_full_reverse_index(json_dir):
    """用个股板块全景.json 补齐反向索引 (覆盖个股全景中的全部板块)"""
    path = os.path.join(json_dir, '个股板块全景.json')
    data = _load_json(path)
    if not data:
        logger.warning('个股板块全景文件为空或不存在')
        return

    added = 0
    for stock_code, sectors in data.items():
        existing = {s['block_code'] for s in _stock_to_sectors.get(stock_code, [])}
        for s in (sectors or []):
            bc = s.get('code', '')
            if not bc or bc in existing:
                continue
            # 从 _sector_meta 查 sector_type
            meta = _sector_meta.get(bc, {})
            st = meta.get('sector_type', '')
            if not st:
                # 从 type 字段反推
                raw_type = s.get('type', '')
                st = _CATEGORY_TYPE_MAP.get(raw_type, 'index')
            _stock_to_sectors.setdefault(stock_code, []).append({
                'block_code': bc,
                'block_name': meta.get('sector_name', s.get('name', '')),
                'sector_type': st,
            })
            existing.add(bc)
            added += 1
    if added:
        logger.info('全景补齐反向索引 %d 条', added)


def get_stock_sectors(stock_code):
    """查个股所属全部板块 (行业·概念·地域·风格·指数)

    Args:
        stock_code: 股票代码, 如 '000001.SZ'
    Returns:
        list[dict]: [{block_code, block_name, sector_type}, ...]
    """
    return list(_stock_to_sectors.get(stock_code, []))


def get_stock_name(stock_code):
    """查股票中文名称

    Args:
        stock_code: 股票代码, 如 '002747.SZ'
    Returns:
        str: 中文名称, 如 '埃斯顿'; 未找到时返回 stock_code 本身
    """
    info = _name_data.get(stock_code)
    if info:
        return info.get('name', stock_code)
    return stock_code


def get_sector_stocks(block_code):
    """查板块内全部个股

    依次在 industry/concept/region/style/index 五张正向映射中查找。

    Args:
        block_code: 板块代码, 如 '881002.SH'
    Returns:
        list[dict]: [{code, name}, ...]; 未找到返回 []
    """
    for m in (_map_industry_stock, _map_concept_stock,
              _map_region_stock, _map_style_stock, _map_index_stock):
        if block_code in m:
            return list(m[block_code])
    return []


def get_sector_raw_type(block_code):
    """获取板块的原始分类类型 (行业一级/行业二级/行业三级/概念板块/...)

    Args:
        block_code: 板块代码
    Returns:
        str: 原始分类类型, 未找到返回 ''
    """
    return _sector_raw_type.get(block_code, '')


def get_stock_industry(stock_code):
    """查个股行业三级分类

    Args:
        stock_code: 股票代码
    Returns:
        dict: {industry_l1, industry_l2, industry_l3,
               industry_l1_code, industry_l2_code, industry_l3_code}
        未找到返回 {}
    """
    return dict(_stock_industry.get(stock_code, {}))


def sync_to_db(con):
    """同步内存映射到 QuestDB

    写入表:
      - qd_sector_meta         (板块元数据)
      - qd_stock_industry      (个股三级分类)
      - qd_map_concept_stock   (概念-个股, 用 block_name 作 concept_name)
      - qd_map_region_stock    (地域-个股, 用 block_name 作 region)
      - qd_map_style_stock     (风格-个股, 用 block_name 作 style)
      - qd_map_index_stock     (指数-个股, 用 block_code 作 index_code)

    注: 行业板块-个股无独立映射表 (通过 qd_stock_industry 三级分类体现)。

    Args:
        con: psycopg2 连接 (autocommit=True)
    Returns:
        dict: 各表写入行数
    """
    from datetime import datetime
    from lib.qdb import executemany_batch

    now = datetime.now()
    counts = {}

    # 1. qd_sector_meta
    rows = []
    for code, m in _sector_meta.items():
        rows.append((code, m['sector_name'], m['sector_type'],
                     now, m['stock_count'], ''))
    counts['qd_sector_meta'] = executemany_batch(
        con, 'qd_sector_meta',
        ['sector_code', 'sector_name', 'sector_type',
         'update_time', 'stock_count', 'description'], rows)

    # 2. qd_stock_industry
    rows = []
    for sc, ind in _stock_industry.items():
        rows.append((sc, now,
                     ind.get('industry_l1', ''), ind.get('industry_l2', ''),
                     ind.get('industry_l3', '')))
    counts['qd_stock_industry'] = executemany_batch(
        con, 'qd_stock_industry',
        ['code', 'update_time', 'industry_l1', 'industry_l2', 'industry_l3'], rows)

    # 3. qd_map_concept_stock (concept_name ← block_name)
    rows = []
    for code, stocks in _map_concept_stock.items():
        name = _sector_meta.get(code, {}).get('sector_name', '')
        for s in stocks:
            rows.append((name, s['code'], now, None))
    counts['qd_map_concept_stock'] = executemany_batch(
        con, 'qd_map_concept_stock',
        ['concept_name', 'code', 'update_time', 'weight'], rows)

    # 4. qd_map_region_stock (region ← block_name)
    rows = []
    for code, stocks in _map_region_stock.items():
        name = _sector_meta.get(code, {}).get('sector_name', '')
        for s in stocks:
            rows.append((name, s['code'], now))
    counts['qd_map_region_stock'] = executemany_batch(
        con, 'qd_map_region_stock',
        ['region', 'code', 'update_time'], rows)

    # 5. qd_map_style_stock (style ← block_name)
    rows = []
    for code, stocks in _map_style_stock.items():
        name = _sector_meta.get(code, {}).get('sector_name', '')
        for s in stocks:
            rows.append((name, s['code'], now))
    counts['qd_map_style_stock'] = executemany_batch(
        con, 'qd_map_style_stock',
        ['style', 'code', 'update_time'], rows)

    # 6. qd_map_index_stock (index_code ← block_code)
    rows = []
    for code, stocks in _map_index_stock.items():
        for s in stocks:
            rows.append((code, s['code'], now, None))
    counts['qd_map_index_stock'] = executemany_batch(
        con, 'qd_map_index_stock',
        ['index_code', 'code', 'update_time', 'weight'], rows)

    return counts
