import subprocess, os

os.chdir(r'K:\QuestDB_test')

# 1. stage
subprocess.run(['git', 'add', '-A'], check=True)
print('staged')

# 2. commit
msg = """fix: DDL字段名对齐 + 策略执行顺序 + scheduler子进程管理

- resonance/sector_flow: block_code→code, net_flow→main_net
- intraday_loop: 调换_run_sector_flow在_run_resonance之前(共振依赖sector_flow_df)
- p08_dark_money: cancel_diff/wtb不在DDL,改用dark_money/pressure字段计算
- p15/p16_stop: cost_price→entry_price(DDL列名对齐)
- p12_big_order: zjl→Zjl(大小写)
- scheduler: _attach_if_running直接杀旧子进程,不返回复用
- docs: 新增HANDOVER.md(面向AI/接手开发者的设计文档)"""
result = subprocess.run(['git', 'commit', '-m', msg], capture_output=True, text=True)
print('stdout:', result.stdout)
print('stderr:', result.stderr)
print('returncode:', result.returncode)
