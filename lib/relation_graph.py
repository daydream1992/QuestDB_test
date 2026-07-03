"""6 维关系图谱

脚本路径: K:\QuestDB_test\\lib\\relation_graph.py
用途: 加载 / 查询 / 同步 板块-个股映射 (行业·概念·地域·风格·指数) + 个股行业三级分类
依赖: psycopg2, lib.qdb
数据源: K:\\QuestDB_test\\指数板块个股映射\\*.json
入库表:
  - qd_sector_meta          板块元数据
  - qd_stock_industry       个股申万三级分类
  - qd_map_concept_stock    概念板块-个股
  - qd_map_region_stock     地域板块-个股
  - qd_map_style_stock      风格板块-个股
  - qd_map_index_stock      指数-成份股
说明:
  - JSON 文件名带时间戳, load_from_json 用 glob 取最新
  - 内存维护正向 (板块->个股) 与反向 (个股->板块) 索引
  - 行业板块-个股直接映射无独立表 (通过 qd_stock_industry 三级分类体现)
"""

import os
import glob
import json
import logging

logger = logging.getLogger(__name__)

# JSON 默认目录
DEFAULT_JSON_DIR = r'K:\QuestDB_test\指数板块个股映射'

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


def _latest_file(json_dir, pattern, exclude_sub=None):
    """glob 匹配, 返回最新文件 (按修改时间)

    Args:
        json_dir: 目录
        pattern: glob 模式 (如 '行业板块_个股_*.json')
        exclude_sub: 文件名中需排除的子串
    """
    files = glob.glob(os.path.join(json_dir, pattern))
    if exclude_sub:
        files = [f for f in files if exclude_sub not in os.path.basename(f)]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _load_json(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_sector_list(json_dir, pattern, sector_type):
    """加载板块列表, 写入 _sector_meta (stock_count 暂为 0, 后续按成分股补)"""
    path = _latest_file(json_dir, pattern)
    data = _load_json(path)
    for blk in data:
        code = blk.get('block_code', '')
        if not code:
            continue
        _sector_meta[code] = {
            'sector_name': blk.get('block_name', ''),
            'sector_type': sector_type,
            'stock_count': 0,
        }


def _load_sector_stocks(json_dir, pattern, target_map, sector_type):
    """加载板块-个股映射, 同时回填 _sector_meta.stock_count 与反向索引"""
    path = _latest_file(json_dir, pattern)
    data = _load_json(path)
    for blk in data:
        code = blk.get('block_code', '')
        name = blk.get('block_name', '')
        stocks = blk.get('stocks', []) or []
        if not code:
            continue
        target_map[code] = [{'code': s.get('code', ''), 'name': s.get('name', '')}
                            for s in stocks]
        # 回填板块元数据
        if code in _sector_meta:
            _sector_meta[code]['stock_count'] = len(stocks)
        else:
            _sector_meta[code] = {
                'sector_name': name,
                'sector_type': sector_type,
                'stock_count': len(stocks),
            }
        # 反向索引
        for s in stocks:
            sc = s.get('code', '')
            if sc:
                _stock_to_sectors.setdefault(sc, []).append({
                    'block_code': code,
                    'block_name': name,
                    'sector_type': sector_type,
                })


def load_from_json(json_dir=None):
    """从 JSON 加载映射到内存

    Args:
        json_dir: JSON 目录, 默认 K:\\QuestDB_test\\指数板块个股映射
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

    # 1. 板块列表 (元数据)
    _load_sector_list(json_dir, '行业板块列表_*.json', 'industry')
    _load_sector_list(json_dir, '概念板块列表_*.json', 'concept')
    _load_sector_list(json_dir, '地区板块列表_*.json', 'region')
    _load_sector_list(json_dir, '风格板块列表_*.json', 'style')
    _load_sector_list(json_dir, '指数板块列表_*.json', 'index')

    # 2. 板块-个股映射 (含反向索引)
    _load_sector_stocks(json_dir, '行业板块_个股_*.json',
                        _map_industry_stock, 'industry')
    _load_sector_stocks(json_dir, '概念板块_个股_*.json',
                        _map_concept_stock, 'concept')
    _load_sector_stocks(json_dir, '地区板块_个股_*.json',
                        _map_region_stock, 'region')
    _load_sector_stocks(json_dir, '风格板块_个股_*.json',
                        _map_style_stock, 'style')
    _load_sector_stocks(json_dir, '指数板块_个股_*.json',
                        _map_index_stock, 'index')

    # 3. 个股行业三级分类 (排除文件名含 "个股" 的变体)
    path = _latest_file(json_dir, '股票行业三级分类_*.json', exclude_sub='个股')
    data = _load_json(path)
    for row in data:
        sc = row.get('stock_code', '')
        if not sc:
            continue
        _stock_industry[sc] = {
            'industry_l1': row.get('行业一级', ''),
            'industry_l2': row.get('行业二级', ''),
            'industry_l3': row.get('行业三级', ''),
            'industry_l1_code': row.get('行业一级_代码', ''),
            'industry_l2_code': row.get('行业二级_代码', ''),
            'industry_l3_code': row.get('行业三级_代码', ''),
        }

    logger.info('关系图谱加载完成: 板块=%d, 个股行业=%d, 反向索引=%d',
                len(_sector_meta), len(_stock_industry), len(_stock_to_sectors))


def get_stock_sectors(stock_code):
    """查个股所属全部板块 (行业·概念·地域·风格·指数)

    Args:
        stock_code: 股票代码, 如 '000001.SZ'
    Returns:
        list[dict]: [{block_code, block_name, sector_type}, ...]
    """
    return list(_stock_to_sectors.get(stock_code, []))


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
