"""验证 p08 dark_money topN 修复"""
import sys
sys.path.insert(0, '.')
import pandas as pd
from lib.qdb import connect, query_df

con = connect()

# 模拟一个全市场数据集（基于当前真实数据）
df = query_df(con, """
SELECT code, dark_money, buy_pressure, sell_pressure
FROM qd_money_flow
WHERE flow_time > dateadd('m', -5, now())
""")
print(f"取到 {len(df)} 条 qd_money_flow 记录")

if df.empty:
    print("无数据,使用模拟数据测试")
    import random
    random.seed(42)
    df = pd.DataFrame({
        'code': [f'{600000+i:06d}.SH' for i in range(200)],
        'dark_money': [random.uniform(-100, 100) for _ in range(200)],
        'buy_pressure': [random.uniform(5, 50) for _ in range(200)],
        'sell_pressure': [random.uniform(1, 30) for _ in range(200)],
    })
    print(f"模拟数据: {len(df)} 条")

# 用修复后的阈值测试
CANCEL_DIFF_MIN = 50.0
WTB_MIN = 10.0
TOP_N = 20

candidates = []
for _, r in df.iterrows():
    cd = abs(float(r.get('dark_money', 0) or 0))
    bp = float(r.get('buy_pressure', 0) or 0)
    sp = float(r.get('sell_pressure', 0) or 0)
    wtb = bp / sp if sp > 0 else 0.0
    if cd > CANCEL_DIFF_MIN and wtb > WTB_MIN:
        score = min(100.0, 50.0 + cd * 0.5)
        candidates.append({'code': r['code'], 'score': score, 'cd': cd, 'wtb': wtb})

print(f"\n阈值修复后:")
print(f"  CANCEL_DIFF_MIN={CANCEL_DIFF_MIN}, WTB_MIN={WTB_MIN}")
print(f"  命中数: {len(candidates)} (修复前阈值下命中会非常多)")

# topN
candidates.sort(key=lambda d: d['score'], reverse=True)
top_n = candidates[:TOP_N]
print(f"  topN 限制: {TOP_N}")
print(f"  最终推送: {len(top_n)} 条")

if top_n:
    print(f"\nTop 5 预览:")
    for c in top_n[:5]:
        print(f"  {c['code']} score={c['score']:.1f} cd={c['cd']:.0f} wtb={c['wtb']:.2f}")

# 对比旧阈值
OLD_CANCEL = 10.0
OLD_WTB = 1.2
old_count = sum(1 for _, r in df.iterrows()
                if abs(float(r.get('dark_money', 0) or 0)) > OLD_CANCEL
                and (float(r.get('buy_pressure', 0) or 0) /
                     (float(r.get('sell_pressure', 0) or 1)) if float(r.get('sell_pressure', 0) or 0) > 0 else 0) > OLD_WTB)
print(f"\n修复对比:")
print(f"  旧阈值命中: {old_count} 条")
print(f"  新阈值命中: {len(candidates)} 条 (topN={TOP_N})")
print(f"  减少: {old_count - len(top_n)} 条 ({100*(old_count-len(top_n))/max(old_count,1):.1f}%)")

con.close()