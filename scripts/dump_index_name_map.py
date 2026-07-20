#!/usr/bin/env python3
"""代码↔名称 映射校验脚本

用途:
  通过 tqcenter 4 个接口, 沉淀权威的代码→中文名称映射,
  下次校验指数/板块代码时直接对照此表, 避免"999999 是上证"类错误。

输出:
  data/market_data/索引代码映射.csv
  控制台打印汇总表

执行:
  python scripts/dump_index_name_map.py
"""
import os
import sys
import csv
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger

OUT_DIR = os.path.join(_PROJ_ROOT, 'data', 'market_data')
os.makedirs(OUT_DIR, exist_ok=True)
OUT_CSV = os.path.join(OUT_DIR, '索引代码映射.csv')


def _normalize_item(item):
    """兼容多种返回格式: dict / list / tuple"""
    if isinstance(item, dict):
        code = item.get('code') or item.get('Code') or ''
        name = item.get('name') or item.get('Name') or ''
        return code, name
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return item[0], item[1]
    return '', ''


def _fetch():
    """从 tqcenter 拉取所有指数/板块代码→中文名

    覆盖 4 个接口:
      1. tq.get_stock_list(market=10)         → 所有板块指数 (880001-880008)
      2. tq.get_sector_list(list_type=1)      → A股板块代码列表
      3. tq.get_stock_info(code)              → 单个证券名 (兜底)
      4. tq.get_stock_list_in_sector(880001)  → 校验 880001 是总市值指数
    """
    from lib.tq_client import safe_call
    from tqcenter import tq  # noqa

    rows = []  # (code, name, src)

    # 1. 所有板块指数 (880001-880008) - 重点
    try:
        lst = safe_call(tq.get_stock_list, market=10, list_type=1) or []
        for item in lst:
            code, name = _normalize_item(item)
            if code:
                rows.append((code, name, 'get_stock_list(market=10)'))
    except Exception as e:
        logger.warning('get_stock_list(market=10) 失败: {}', e)

    # 2. A股板块代码列表
    try:
        lst = safe_call(tq.get_sector_list, list_type=1) or []
        for item in lst:
            code, name = _normalize_item(item)
            if code:
                rows.append((code, name, 'get_sector_list(list_type=1)'))
    except Exception as e:
        logger.warning('get_sector_list 失败: {}', e)

    # 3. 关键指数代码逐个查 (兜底)
    KEY_CODES = [
        '999999.SH', '000001.SH', '399001.SZ', '399006.SZ',
        '000300.SH', '000688.SH',
        '880001.SH', '880002.SH', '880003.SH', '880004.SH',
        '880005.SH', '880006.SH', '880007.SH', '880008.SH',
    ]
    for code in KEY_CODES:
        try:
            info = safe_call(tq.get_stock_info, stock_code=code, field_list=['Name']) or {}
            name = info.get('Name') or info.get('name') or ''
            if name:
                rows.append((code, name, 'get_stock_info'))
        except Exception as e:
            logger.warning('get_stock_info({}) 失败: {}', code, e)

    # 4. 校验 880001 是总市值指数 (返回成份股列表, 用数量佐证)
    try:
        lst = safe_call(tq.get_stock_list_in_sector, block_code='880001.SH', list_type=1) or []
        rows.append(('880001.SH', f'总市值指数(含{len(lst)}只成份股)', 'get_stock_list_in_sector'))
    except Exception as e:
        logger.warning('get_stock_list_in_sector(880001) 失败: {}', e)

    return rows


def _dedup(rows):
    """去重: 同一 code 聚合多来源, name 不一致时拼接"""
    seen = {}
    for code, name, src in rows:
        if not code or not name:
            continue
        if code not in seen:
            seen[code] = {'names': set(), 'srcs': set()}
        seen[code]['names'].add(name)
        seen[code]['srcs'].add(src)
    return seen


def main():
    logger.info('===== 拉取 tqcenter 代码↔名称映射 =====')
    raw = _fetch()
    if not raw:
        logger.error('所有接口都失败, 无法生成映射表')
        return 1

    dedup = _dedup(raw)

    # 写入 CSV
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(OUT_CSV, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['代码', '中文名', '来源接口数', '来源接口列表', '查询时间'])
        for code in sorted(dedup.keys()):
            d = dedup[code]
            names = ' / '.join(sorted(d['names']))
            srcs = ' | '.join(sorted(d['srcs']))
            w.writerow([code, names, len(d['srcs']), srcs, ts])

    # 控制台打印
    print('\n===== 代码↔中文名 映射表 (去重后) =====')
    print(f'{"代码":<14} {"中文名":<28} {"来源数":<6} 名称是否一致')
    print('-' * 75)
    for code in sorted(dedup.keys()):
        d = dedup[code]
        names = sorted(d['names'])
        disp = ' / '.join(names)
        if len(disp) > 28:
            disp = disp[:26] + '..'
        consistent = '✓' if len(names) == 1 else '✗ 冲突'
        print(f'{code:<14} {disp:<28} {len(d["srcs"]):<6} {consistent}')
    print(f'\n总计: {len(dedup)} 个代码, 落盘 → {OUT_CSV}')
    return 0


if __name__ == '__main__':
    sys.exit(main())