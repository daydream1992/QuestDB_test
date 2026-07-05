"""scripts.data_inventory_rules: 数据资产盘点 - 补业务规则

脚本路径: K:\\QuestDB_test\\scripts\\data_inventory_rules.py
用途: 用户授权威规则, 记入 inventory 字段 capability 与顶层 business_rules
依赖: json, _deprecated/inventory/data_inventory.json (读)
输出: 同上 (原地补充)
用法: python scripts/data_inventory_rules.py
入参: 无
返回: 写回 inventory JSON
说明:
  - 涨停跌停用 FCAmo 判定 (>0 涨停/<0 跌停), 非 Now>=ZTPrice
  - 标注代码不一致处 (业务侧引用与 inventory 规则)
  - 必须先跑 data_inventory.py 生成骨架
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try: sys.stdout.reconfigure(encoding='utf-8')
except: pass

INV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_deprecated', 'inventory', 'data_inventory.json')

# 代码里涨停判定不一致的位置 (grep 盘点)
LIMIT_UP_CODE_STATUS = {
    'compute/k3_sentiment.py::classify_stock': '✓ 一致 (FCAmo>0涨停/<0跌停/=0+Max>=ZTPrice炸板)',
    'strategy/plugins/p01_zt_daban.py': '⚠️ 不一致 (Now>=ZTPrice*0.999, 会选入触价未封的假涨停)',
    'strategy/intraday_engine.py::_check_limit': '⚠️ 不一致 (现价>=ZTPrice 且 Sellv1<=100手, 同问题)',
}

with open(INV, 'r', encoding='utf-8') as f:
    inv = json.load(f)

# 1. FCAmo 字段 capability 补权威判定
fixed = 0
for tbl in ['qd_stock_snapshot', 'qd_stock_daily', 'qd_stock_intraday']:
    for fld in inv['tables'][tbl]['fields']:
        if fld['name'] == 'FCAmo':
            fld['capability'] = ('封单额(万元); 权威涨跌停判定: FCAmo>0涨停 / <0跌停 / =0未封'
                                 '(可能 Now>=ZTPrice 触价但无封单=炸板风险)')
            fld['authoritative_rule'] = True
            fixed += 1

# 2. 顶层业务规则
inv.setdefault('business_rules', [])
# 去重更新
inv['business_rules'] = [r for r in inv['business_rules'] if r.get('rule') != '涨停跌停判定']
inv['business_rules'].append({
    'rule': '涨停跌停判定',
    'authoritative': True,
    'source': '用户(业务权威) + get_more_info 取 FCAmo',
    'logic': 'FCAmo > 0 → 涨停 (有买单封单); FCAmo < 0 → 跌停 (有卖单封单); FCAmo = 0 → 未封板',
    'caveat': 'Now >= ZTPrice 不等于涨停! 可能是触及涨停价但无封单(炸板/假涨停)。p01/intraday_engine 用 Now>=ZTPrice 判定, 会误选假涨停, 待修。',
    'code_status': LIMIT_UP_CODE_STATUS,
})

with open(INV, 'w', encoding='utf-8') as f:
    json.dump(inv, f, ensure_ascii=False, indent=2)
print(f'FCAmo 补权威判定 {fixed} 处 + business_rules, → {INV}', file=sys.stderr)