#!/usr/bin/env python3
"""
市场数据查询模块

用法:
    from 市场数据查询 import MarketData
    m = MarketData()

    m.get_name('600519.SH')               # 代码→中文名
    m.get_stock_blocks('000001.SZ')       # 股票→所有归属板块
    m.get_block_stocks('880506.SH')       # 板块→成分股列表
    m.get_stock_industry('000001.SZ')      # 股票→行业链
    m.batch_names(['600519.SH', '000001.SZ'])  # 批量查名称
    m.find_block_by_name('5G')            # 按名称模糊搜索板块
    m.get_industry_tree()                 # 获取行业层级树
    m.get_block_index()                   # 获取板块索引（code→type）
    m.get_block_categories('880506.SH')    # 板块code→所有分类（如同时是概念和指数）
    m.reload()                            # 重新加载数据
    m.stats()                             # 数据统计摘要
"""
import json
import os
from pathlib import Path

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '市场数据')


class MarketData:

    def __init__(self, data_dir=None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self._name_map = None
        self._block_index = None
        self._industry_tree = None
        self._stock_industry = None
        self._stock_panorama = None
        self._concept_stocks = None
        self._style_stocks = None
        self._region_stocks = None
        self._concept_blocks = None
        self._style_blocks = None
        self._region_blocks = None
        self._index_stocks = None
        self._index_list = None
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        base = self.data_dir
        self._name_map = self._read('名称映射.json')
        self._block_index = self._read('板块索引.json')
        self._industry_tree = self._read(os.path.join('行业', '行业层级树.json'))
        self._stock_industry = self._read(os.path.join('行业', '股票行业映射.json'))
        self._stock_panorama = self._read('个股板块全景.json')
        self._concept_stocks = self._read(os.path.join('概念', '概念成分股.json'))
        self._style_stocks = self._read(os.path.join('风格', '风格成分股.json'))
        self._region_stocks = self._read(os.path.join('地区', '地区成分股.json'))
        self._concept_blocks = self._read(os.path.join('概念', '概念板块列表.json'))
        self._style_blocks = self._read(os.path.join('风格', '风格板块列表.json'))
        self._region_blocks = self._read(os.path.join('地区', '地区板块列表.json'))
        self._index_stocks = self._read(os.path.join('指数', '指数成分股.json'))
        self._index_list = self._read(os.path.join('指数', '指数列表.json'))
        self._loaded = True

    def _read(self, rel_path):
        path = os.path.join(self.data_dir, rel_path)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {} if '成分股' in rel_path or rel_path == '个股板块全景.json' else None

    def reload(self):
        self._loaded = False
        self._load()

    @property
    def name_map(self):
        if self._name_map is None:
            self._load()
        return self._name_map

    @property
    def block_index(self):
        if self._block_index is None:
            self._load()
        return self._block_index

    @property
    def industry_tree(self):
        if self._industry_tree is None:
            self._load()
        return self._industry_tree

    @property
    def stock_industry(self):
        if self._stock_industry is None:
            self._load()
        return self._stock_industry

    @property
    def stock_panorama(self):
        if self._stock_panorama is None:
            self._load()
        return self._stock_panorama

    # ----------------------------------------------------------
    # 核心查询
    # ----------------------------------------------------------

    def get_name(self, code):
        """代码 → 中文名和类型"""
        return self.name_map.get(code)

    def batch_names(self, codes):
        """批量查询名称"""
        return [
            {'code': c, **self.name_map.get(c, {'name': '', 'type': ''})}
            for c in codes
        ]

    def get_stock_blocks(self, code):
        """股票 → 所有归属板块"""
        return self.stock_panorama.get(code, [])

    def get_block_stocks(self, block_code):
        """板块代码 → 成分股列表"""
        info = self.block_index.get(block_code, {})
        cats = info.get('categories', [])

        if not cats:
            return None

        if '行业' in cats:
            return self._get_industry_stocks(block_code)
        elif '概念' in cats:
            return self._concept_stocks.get(block_code)
        elif '风格' in cats:
            return self._style_stocks.get(block_code)
        elif '地区' in cats:
            return self._region_stocks.get(block_code)
        elif '指数' in cats:
            return self._index_stocks.get(block_code)
        return None

    def _get_industry_stocks(self, block_code):
        """从股票行业映射反向推行业成分股"""
        result = []
        for sc, levels in self.stock_industry.items():
            for level in [1, 2, 3]:
                if levels.get(level, {}).get('code') == block_code:
                    result.append(sc)
                    break
        return result if result else None

    def get_stock_industry(self, code):
        """股票 → 行业链"""
        return self.stock_industry.get(code, {})

    def get_industry_tree(self):
        """获取完整行业层级树"""
        return self.industry_tree

    def get_block_index(self):
        """获取板块索引"""
        return self.block_index

    def get_block_categories(self, block_code):
        """板块code → 所有分类 [{type, category}]"""
        info = self.block_index.get(block_code, {})
        return info.get('categories', [])

    # ----------------------------------------------------------
    # 搜索
    # ----------------------------------------------------------

    def find_block_by_name(self, keyword, category=None):
        """
        按板块名称模糊搜索
        category: '行业'/'概念'/'风格'/'地区'/'指数'，不传则全部
        """
        kw = keyword.lower()
        result = []
        for code, info in self.block_index.items():
            name = info.get('name', '')
            if kw in name.lower():
                cats = info.get('categories', [])
                if category and category not in cats:
                    continue
                result.append({'code': code, 'name': name, 'categories': cats})
        return result

    def find_stock_by_name(self, keyword):
        """按股票名称模糊搜索"""
        kw = keyword.lower()
        return [
            {'code': code, **info}
            for code, info in self.name_map.items()
            if kw in info.get('name', '').lower()
        ]

    def find_stocks_in_blocks(self, block_names, match_all=False):
        """
        在指定板块中的股票
        block_names: 板块名称列表
        match_all: True=同时属于所有板块, False=任一属于即可
        """
        block_codes = []
        for name in block_names:
            matches = self.find_block_by_name(name)
            if matches:
                block_codes.append(matches[0]['code'])

        if not block_codes:
            return []

        stock_sets = []
        for bc in block_codes:
            stocks = self.get_block_stocks(bc)
            stock_sets.append(set(stocks) if stocks else set())

        if match_all:
            result = stock_sets[0]
            for s in stock_sets[1:]:
                result &= s
        else:
            result = set()
            for s in stock_sets:
                result |= s
        return sorted(result)

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------

    def stats(self):
        """数据统计摘要"""
        return {
            '名称映射': len(self.name_map),
            '板块索引': len(self.block_index),
            '股票行业映射': len(self.stock_industry),
            '个股板块全景': len(self.stock_panorama),
            '概念板块': len(self._concept_blocks) if self._concept_blocks else 0,
            '风格板块': len(self._style_blocks) if self._style_blocks else 0,
            '地区板块': len(self._region_blocks) if self._region_blocks else 0,
            '指数列表': len(self._index_list) if self._index_list else 0,
            '行业一级': len(self.industry_tree),
            '行业二级': sum(len(l1['children']) for l1 in self.industry_tree),
            '行业三级': sum(len(l2['children']) for l1 in self.industry_tree for l2 in l1['children']),
        }


# ----------------------------------------------------------
# 快捷函数
# ----------------------------------------------------------
_md = None

def _get_instance():
    global _md
    if _md is None:
        _md = MarketData()
    return _md

def get_name(code): return _get_instance().get_name(code)
def batch_names(codes): return _get_instance().batch_names(codes)
def get_stock_blocks(code): return _get_instance().get_stock_blocks(code)
def get_block_stocks(block_code): return _get_instance().get_block_stocks(block_code)
def get_stock_industry(code): return _get_instance().get_stock_industry(code)
def get_industry_tree(): return _get_instance().get_industry_tree()
def get_block_index(): return _get_instance().get_block_index()
def get_block_categories(code): return _get_instance().get_block_categories(code)
def find_block_by_name(keyword, category=None): return _get_instance().find_block_by_name(keyword, category)
def find_stock_by_name(keyword): return _get_instance().find_stock_by_name(keyword)
def find_stocks_in_blocks(block_names, match_all=False): return _get_instance().find_stocks_in_blocks(block_names, match_all)
def stats(): return _get_instance().stats()
def reload(): _get_instance().reload()


if __name__ == '__main__':
    m = MarketData()

    print('=== 统计 ===')
    for k, v in m.stats().items():
        print(f'  {k}: {v}')

    print('\n=== 代码→名称 ===')
    print(m.get_name('600519.SH'))
    print(m.get_name('880506.SH'))

    print('\n=== 板块分类 ===')
    print(m.get_block_categories('880506.SH'))
    print(m.get_block_categories('881385.SH'))

    print('\n=== 板块→成分股 ===')
    print((m.get_block_stocks('880506.SH') or [])[:5])

    print('\n=== 行业链 ===')
    print(m.get_stock_industry('000001.SZ'))

    print('\n=== 按名称搜索板块 ===')
    print(m.find_block_by_name('5G'))
    print(m.find_block_by_name('银行'))

    print('\n=== 按名称搜索股票 ===')
    print(m.find_stock_by_name('茅台')[:3])

    print('\n=== 交集搜索：银行+大盘 ===')
    print(m.find_stocks_in_blocks(['银行'], match_all=False)[:5])
