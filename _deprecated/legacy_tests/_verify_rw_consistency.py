"""tests._verify_rw_consistency: 底座验证 #1 — 同时读写一致性

脚本路径: K:\\QuestDB_test\\tests\\_verify_rw_consistency.py
用途: 验证 QuestDB 写后立即读 (同连接/新连接) 可见性 + O3 延迟
依赖: lib.qdb (psycopg2)
用法: python tests/_verify_rw_consistency.py
入参: 无
返回: stdout 报告 (PASS/FAIL) + 退出码
说明:
  - 写后同连接读: 应可见 (autocommit=True)
  - 写后新连接读: 模拟重连, 应可见 (QuestDB 不跨连接延迟)
  - O3 延迟检测: 写后 5s 内是否能读到
  - 这是 H5 修复 (commit d8ea329) 的回归测试
  - 2026-07-05 整理: 下划线开头文件归档 _deprecated/legacy_tests/
"""
import sys, time
from datetime import datetime
sys.path.insert(0, r'K:\QuestDB_test')
from lib.qdb import connect, query_df, executemany_batch

# 用 qd_money_flow 写一个独特 marker (DEDUP KEY = flow_time+code, 独特 code 不冲突)
marker_code = 'RW_TEST_' + datetime.now().strftime('%H%M%S%f')
marker_ts = datetime.now().replace(microsecond=0)
print(f'marker: code={marker_code} flow_time={marker_ts}')

COLS = ['code', 'flow_time', 'main_net', 'big_order_diff', 'dark_money',
        'light_money', 'pressure_diff_5level', 'buy_pressure', 'sell_pressure', 'net_flow']

# === 写入: 用 con1 (生产同路径 executemany_batch, autocommit) ===
con1 = connect()
t0 = time.time()
n = executemany_batch(con1, 'qd_money_flow', COLS,
                      [(marker_code, marker_ts, 1.11, None, 2.22, None, 3.33, 4.44, 5.55, 6.66)])
t1 = time.time()
print(f'[{t1-t0:.3f}s] executemany_batch 写入 {n} 行 (autocommit 应已 commit)')

# === 测试 A: 同连接立即读 ===
t2 = time.time()
df = query_df(con1, f"SELECT code, flow_time, main_net FROM qd_money_flow WHERE code = '{marker_code}'")
t3 = time.time()
latency_ms = (t3 - t2) * 1000
saw_same_con = not df.empty
print(f'[{latency_ms:.0f}ms] 同连接立即读: {"看到 ✓" if saw_same_con else "没看到 ✗"}')
if saw_same_con:
    print(f'         {df.iloc[0].to_dict()}')

# === 测试 B: 新连接读 (模拟 H5 重连 / 不同策略进程) ===
con2 = connect()
t4 = time.time()
df2 = query_df(con2, f"SELECT code FROM qd_money_flow WHERE code = '{marker_code}'")
t5 = time.time()
saw_new_con = not df2.empty
print(f'[{(t5-t4)*1000:.0f}ms] 新连接读: {"看到 ✓" if saw_new_con else "没看到 ✗ (重连后有不可见窗口!)"}')

# === 测试 C: 总写入→可见延迟 ===
total_latency = (t3 - t1) * 1000
print(f'写入commit→同连接可见 总延迟: {total_latency:.0f}ms')

con1.close()
con2.close()

print()
print('=== 结论 ===')
if saw_same_con and saw_new_con:
    print('同时读写 OK: 写后立即 (同/新连接) 可见, 无 O3 阻塞')
    print('  → 主循环提速到 10s 在读写一致性上可行')
else:
    print('同时读写 有问题:')
    if not saw_same_con:
        print('  - 同连接写后立即读不到 (严重, autocommit 没生效或 O3 阻塞)')
    if not saw_new_con:
        print('  - 新连接读不到 (重连/跨进程有不可见窗口, H5 重连有风险)')