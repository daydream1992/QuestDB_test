#!/usr/bin/env python3
"""
全市场数据架构导出（定期更新版）

market 代码参考 tdxdata_test.py:
  5:所有A股  12:概念板块  13:风格板块  14:地区板块
  16:行业一级  17:行业二级  18:行业三级
  15:沪深指数  31:ETF基金  32:可转债
  51:创业板  52:科创板  53:北交所

输出目录: ./市场数据/
  名称映射.json              # code→{name, type}
  板块索引.json              # block_code→{name, categories:[{type,category}]}
  行业/
  ├── 行业层级树.json        # 纯层级树
  └── 股票行业映射.json      # stock→{level1, level2, level3}
  概念/
  ├── 概念板块列表.json      # [{code, name}]
  └── 概念成分股.json        # {block_code: [stock_codes]}
  风格/
  ├── 风格板块列表.json
  └── 风格成分股.json
  地区/
  ├── 地区板块列表.json
  └── 地区成分股.json
  指数/
  ├── 指数列表.json
  └── 指数成分股.json
  个股板块全景.json          # stock→[{code, name, type}]
"""
import sys
import os
import json
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, r'K:\txdlianghua\PYPlugins\user')
from tqcenter import tq
tq.initialize(__file__)

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '市场数据')
os.makedirs(BASE, exist_ok=True)

def save_json(data, rel_path):
    path = os.path.join(BASE, rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data) if isinstance(data, (list, dict)) else 0
    size = os.path.getsize(path)
    sz = f'{size/1048576:.1f}MB' if size > 1048576 else f'{size/1024:.0f}KB'
    print(f'  ✓ {rel_path}  ({count}条, {sz})')

def append_block(block_index, code, name, btype, category):
    """板块索引追加（支持同一code多分类）"""
    if not code:
        return
    if code not in block_index:
        block_index[code] = {'name': name, 'categories': []}
    elif block_index[code]['name'] != name:
        # 名称不一致，保留已有的（优先用行业/概念/风格/地区的，更权威）
        pass
    cats = block_index[code]['categories']
    if not any(c['category'] == category for c in cats):
        cats.append({'type': btype, 'category': category})

# ============================================================
# 1. 名称映射
# ============================================================
print('\n=== 1. 名称映射 ===')
name_map = {}

stock_markets = [('5', '主板'), ('51', '创业板'), ('52', '科创板'), ('53', '北交所')]
for market, label in stock_markets:
    items = tq.get_stock_list(market, list_type=1)
    if items:
        for item in items:
            code, name = item.get('Code', ''), item.get('Name', '')
            if code and code not in name_map:
                name_map[code] = {'name': name, 'type': label}
        print(f'  {label}: {len(items)} 只')
    else:
        print(f'  {label}: 获取失败')

for market, label in [('31', 'ETF'), ('32', '可转债')]:
    items = tq.get_stock_list(market, list_type=1)
    if items:
        for item in items:
            code, name = item.get('Code', ''), item.get('Name', '')
            if code and code not in name_map:
                name_map[code] = {'name': name, 'type': label}
        print(f'  {label}: {len(items)} 只')

print(f'  名称映射合计: {len(name_map)} 条')
save_json(name_map, '名称映射.json')

# ============================================================
# 2. 行业
# ============================================================
print('\n=== 2. 行业 ===')
level_blocks = {}
for level, market in [(1, '16'), (2, '17'), (3, '18')]:
    items = tq.get_stock_list(market, list_type=1)
    if items:
        level_blocks[level] = [{'code': b.get('Code', ''), 'name': b.get('Name', '')} for b in items if b.get('Code')]
    else:
        codes = tq.get_stock_list(market) or []
        level_blocks[level] = [{'code': c, 'name': ''} for c in codes]
    print(f'  行业{level}级: {len(level_blocks[level])} 个')

stock_industry = {}
t0 = time.time()
for level in [1, 2, 3]:
    for block in level_blocks[level]:
        bcode, bname = block['code'], block['name']
        if not bcode:
            continue
        stocks = tq.get_stock_list_in_sector(bcode) or []
        for sc in stocks:
            if sc not in stock_industry:
                stock_industry[sc] = {}
            stock_industry[sc][level] = {'code': bcode, 'name': bname}
    print(f'  行业{level}级完成, 耗时 {time.time()-t0:.1f}s')

tree_data = {}
for sc, levels in stock_industry.items():
    l1, l2, l3 = levels.get(1, {}), levels.get(2, {}), levels.get(3, {})
    l1c, l1n = l1.get('code', ''), l1.get('name', '')
    l2c, l2n = l2.get('code', ''), l2.get('name', '')
    l3c, l3n = l3.get('code', ''), l3.get('name', '')
    if l1c and l1c not in tree_data:
        tree_data[l1c] = {'code': l1c, 'name': l1n, 'children': {}}
    if l2c:
        c2 = tree_data[l1c]['children']
        if l2c not in c2:
            c2[l2c] = {'code': l2c, 'name': l2n, 'children': {}}
        if l3c and l3c not in c2[l2c]['children']:
            c2[l2c]['children'][l3c] = {'code': l3c, 'name': l3n}

industry_tree = []
for l1 in sorted(tree_data.values(), key=lambda x: x['code']):
    l1_item = {'code': l1['code'], 'name': l1['name'], 'children': []}
    for l2 in sorted(l1['children'].values(), key=lambda x: x['code']):
        l2_item = {'code': l2['code'], 'name': l2['name'], 'children': []}
        for l3 in sorted(l2['children'].values(), key=lambda x: x['code']):
            l2_item['children'].append({'code': l3['code'], 'name': l3['name']})
        l1_item['children'].append(l2_item)
    industry_tree.append(l1_item)
save_json(industry_tree, '行业/行业层级树.json')

stock_industry_flat = {}
for sc, levels in sorted(stock_industry.items()):
    row = {}
    for level, key in [(1, 'level1'), (2, 'level2'), (3, 'level3')]:
        if level in levels:
            row[key] = {'code': levels[level]['code'], 'name': levels[level]['name']}
    stock_industry_flat[sc] = row
save_json(stock_industry_flat, '行业/股票行业映射.json')

# ============================================================
# 3. 概念/风格/地区 (market 12/13/14)
# ============================================================
BLOCK_CONFIGS = [('概念', '12'), ('风格', '13'), ('地区', '14')]
category_blocks = {}
stock_all_blocks = defaultdict(list)

for label, market in BLOCK_CONFIGS:
    print(f'\n=== 3. {label}板块 (market={market}) ===')
    items = tq.get_stock_list(market, list_type=1)
    if not items:
        codes = tq.get_stock_list(market) or []
        items = [{'Code': c, 'Name': ''} for c in codes]
    blocks = [{'code': b.get('Code', ''), 'name': b.get('Name', '')} for b in items if b.get('Code')]
    category_blocks[label] = blocks
    print(f'  {label}板块: {len(blocks)} 个')

    if not blocks:
        save_json([], f'{label}/{label}板块列表.json')
        save_json({}, f'{label}/{label}成分股.json')
        continue

    save_json(blocks, f'{label}/{label}板块列表.json')

    for b in blocks:
        if b['code'] and b['code'] not in name_map:
            name_map[b['code']] = {'name': b['name'], 'type': label + '板块'}

    block_con = {}
    t0 = time.time()
    for i, b in enumerate(blocks):
        bcode, bname = b['code'], b['name']
        stocks = tq.get_stock_list_in_sector(bcode) or []
        block_con[bcode] = stocks
        for sc in stocks:
            stock_all_blocks[sc].append({'code': bcode, 'name': bname, 'type': label + '板块'})
        if (i + 1) % 50 == 0:
            print(f'  进度 {i+1}/{len(blocks)}, {time.time()-t0:.1f}s')
    print(f'  成分股完成, {time.time()-t0:.1f}s')
    save_json(block_con, f'{label}/{label}成分股.json')

# ============================================================
# 4. 指数
# ============================================================
print('\n=== 4. 指数 ===')
idx_list = []
if items := tq.get_stock_list('15', list_type=1):
    for item in items:
        code, name = item.get('Code', ''), item.get('Name', '')
        if code:
            idx_list.append({'code': code, 'name': name})
save_json(idx_list, '指数/指数列表.json')

idx_con = {}
t0 = time.time()
for i, idx in enumerate(idx_list):
    icode = idx['code']
    stocks = tq.get_stock_list_in_sector(icode) or []
    if stocks:
        idx_con[icode] = stocks
        for sc in stocks:
            stock_all_blocks[sc].append({'code': icode, 'name': idx['name'], 'type': '指数成分'})
    if (i + 1) % 50 == 0:
        print(f'  进度 {i+1}/{len(idx_list)}, {time.time()-t0:.1f}s')
print(f'  成分股完成, {time.time()-t0:.1f}s')
save_json(idx_con, '指数/指数成分股.json')

# ============================================================
# 5. 个股板块全景
# ============================================================
print('\n=== 5. 个股板块全景 ===')
for sc, levels in stock_industry.items():
    for level, lbl in [(1, '行业一级'), (2, '行业二级'), (3, '行业三级')]:
        if level in levels:
            stock_all_blocks[sc].append({'code': levels[level]['code'], 'name': levels[level]['name'], 'type': lbl})

stock_panorama = {}
for sc, blocks in sorted(stock_all_blocks.items()):
    stock_panorama[sc] = blocks
save_json(stock_panorama, '个股板块全景.json')

# ============================================================
# 6. 板块索引（支持多分类）
# ============================================================
print('\n=== 6. 板块索引 ===')
block_index = {}

# 行业
for l1 in industry_tree:
    append_block(block_index, l1['code'], l1['name'], '行业一级', '行业')
    for l2 in l1.get('children', []):
        append_block(block_index, l2['code'], l2['name'], '行业二级', '行业')
        for l3 in l2.get('children', []):
            append_block(block_index, l3['code'], l3['name'], '行业三级', '行业')

# 概念/风格/地区
for label, blocks in category_blocks.items():
    for b in blocks:
        append_block(block_index, b['code'], b['name'], label + '板块', label)

# 指数
for idx in idx_list:
    append_block(block_index, idx['code'], idx['name'], '指数', '指数')

save_json(block_index, '板块索引.json')

# 统计各分类数量
cats = defaultdict(int)
for info in block_index.values():
    for cat in info.get('categories', []):
        cats[cat['category']] += 1
for c, n in sorted(cats.items()):
    print(f'  {c}: {n}')

# 最终名称映射
save_json(name_map, '名称映射.json')

# ============================================================
# 汇总
# ============================================================
print('\n' + '=' * 50)
print('=== 汇总 ===')
print(f'  输出目录: {BASE}')
print(f'  名称映射: {len(name_map)} 条')
print(f'  板块索引: {len(block_index)} 条')
print(f'  行业层级: {len(industry_tree)}级, '
      f'{sum(len(l1["children"]) for l1 in industry_tree)}级, '
      f'{sum(len(l2["children"]) for l1 in industry_tree for l2 in l1["children"])}级')
print(f'  股票行业映射: {len(stock_industry_flat)} 只')
print(f'  个股板块全景: {len(stock_panorama)} 只')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

tq.close()
