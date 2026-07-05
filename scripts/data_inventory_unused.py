"""整理 tqcenter 说明书有但 Q 库未使用的字段 → docs/未使用字段清单.md

价值: 说明书是 tqcenter 全部能力, 库只用了 99/612。
未使用的 500+ 字段里可能藏着能补现有坑/解锁新策略的字段
(如真实连板数、更细龙虎榜、财务数据)。

输出按接口分组, 标注可能有用的高价值字段。
用法: python scripts/data_inventory_unused.py
"""
import os
import re
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 复用 spec 脚本的 parse_spec
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_inventory_spec import parse_spec, parse_ddl  # noqa

INV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_deprecated', 'inventory', 'data_inventory.json')
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_deprecated', 'inventory', '未使用字段清单.md')

# 高价值关键词 (可能补现有坑/解锁策略) - 优先标注
HIGH_VALUE_KEYS = ['涨停', '连板', '跌停', '封单', '封板', '主力', '资金', '竞价', '龙虎',
                   '换手', '量比', '市值', '成交', '开盘', '收盘', '最高', '最低', '净额',
                   '主买', '主卖', '委比', '委差', '大单', '中单', '小单', '游资', '机构',
                   '爆量', '缩量', '新高', '趋势', '强庄', '异动', '缺口']


def main():
    spec_md = parse_spec()  # field -> {chinese, note, api, file}
    spec_ddl = parse_ddl()
    with open(INV_PATH, 'r', encoding='utf-8') as f:
        inv = json.load(f)

    # 库已用字段 (大小写不敏感)
    lib_fields = set()
    for t in inv['tables'].values():
        for f in t['fields']:
            lib_fields.add(f['name'].lower())

    # 未使用 = 说明书有 - 库用
    unused = {}
    for f, info in spec_md.items():
        if f.lower() not in lib_fields:
            unused[f] = info
    # 也补 DDL 提到但库没有的 (一般 DDL 字段都在库, 跳过)

    # 按 api(接口) 分组
    by_api = {}
    for f, info in unused.items():
        api = info.get('api') or '(未分类)'
        by_api.setdefault(api, []).append((f, info))

    # 标高价值
    def is_high(info):
        text = (info.get('chinese', '') + info.get('note', ''))
        return any(k in text for k in HIGH_VALUE_KEYS)

    high_count = sum(1 for info in unused.values() if is_high(info))

    # 输出 md
    lines = [
        '# 未使用字段清单 (tqcenter 能提供但 Q 库未接入)',
        '',
        f'> 说明书共 {len(spec_md)} 字段, 库已用 {len(spec_md) - len(unused)}, '
        f'**未使用 {len(unused)}** (其中可能高价值 {high_count} 个, 标 ⭐)',
        '> 来源: docs/通达信量化平台说明书/ 各接口 md',
        '> 价值: 这些字段 tqcenter 能给但 Q 没用, 可能补现有坑(如真实连板数)或解锁新策略',
        '',
    ]
    # 高价值字段优先列一个总览
    lines.append('## ⭐ 高价值未使用字段总览 (优先评估)')
    lines.append('')
    lines.append('| 字段 | 中文 | 接口 | 说明 |')
    lines.append('|------|------|------|------|')
    for f, info in sorted(unused.items(), key=lambda x: -len(x[1].get('note', ''))):
        if is_high(info):
            lines.append(f"| `{f}` | {info.get('chinese', '')} | {info.get('api', '')} | {info.get('note', '')[:60]} |")
    lines.append('')

    # 按接口分组全列
    lines.append('## 按接口分组的全部未使用字段')
    lines.append('')
    for api in sorted(by_api.keys()):
        fields = by_api[api]
        lines.append(f'### {api} ({len(fields)} 个)')
        lines.append('')
        lines.append('| 字段 | 中文 | 说明 | 高价值 |')
        lines.append('|------|------|------|--------|')
        for f, info in sorted(fields):
            star = '⭐' if is_high(info) else ''
            lines.append(f"| `{f}` | {info.get('chinese', '')} | {info.get('note', '')[:60]} | {star} |")
        lines.append('')

    with open(OUT_PATH, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lines))
    print(f'未使用字段 {len(unused)} (高价值 {high_count}) → {OUT_PATH}', file=sys.stderr)


if __name__ == '__main__':
    main()