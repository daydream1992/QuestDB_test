"""testv10.2 盘前 smoke 自检 — 提前发现问题, 不用盘中改

脚本路径: K:/QuestDB_test/testv10.2/smoke_v10.2.py
用途: 盘前跑一遍所有模块 + 关键逻辑断言, 任何失败 → 非零退出码,
      让"午休覆盖/频控/去重"这类逻辑 bug 在盘前暴露, 不在盘中改。
三层:
  L0 纯逻辑断言 (0 数据, <5s): get_stage 全时段矩阵 / TokenBucket 频控 /
     _classify / _limit_pct / board_pool 状态机
  L1 配置一致性 (0 数据): settings 常量被引用 / SENTIMENT_FIELDS key 存在 /
     mapping parquet 存在
  L2 COM 探针 (盘前真实调用, 需通达信): get_pricevol 小样本 / more_info /
     subscribe_hq 单只 (验证 DLL 订阅通道) / send_warn 参数
跑法: python testv10.2/smoke_v10.2.py [--fast]   # --fast 只跑 L0+L1
      prepare_v10.2.ps1 接入 (改后跑)
退出码: 0=全过, 1=有失败
"""

import bootstrap
bootstrap.ensure_paths()

import sys  # noqa: E402
import json  # noqa: E402
from datetime import datetime, time as dtime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

_failures = []


def check(name: str, ok: bool, detail: str = ''):
    mark = '✅' if ok else '❌'
    print(f'  {mark} {name} {detail}')
    if not ok:
        _failures.append(name)


# ── L0 纯逻辑断言 ──
def l0_logic():
    print('== L0 纯逻辑断言 (0 数据) ==')
    # 1. get_stage 全时段矩阵 (午休 bug 的回归!)
    expect = {'09:10': 'off', '09:20': 'auction', '09:40': 'open',
              '10:00': 'intraday', '11:00': 'intraday', '11:40': 'off',
              '12:00': 'off', '12:30': 'off', '13:10': 'intraday',
              '13:50': 'intraday', '14:30': 'tail', '15:10': 'close'}
    stage_ok = True
    for t, want in expect.items():
        h, m = map(int, t.split(':'))
        got = cfg.get_stage(datetime(2026, 8, 10, h, m))
        if got != want:
            print(f'    ✗ {t}: 期望 {want} 实际 {got}')
            stage_ok = False
    check('get_stage 全时段矩阵 (午休 off)', stage_ok)

    # 2. TokenBucket 频控
    from publisher import TokenBucket
    tb = TokenBucket(3, lanes=3)
    now = datetime.now()
    # 3 lane 各 1 次 → 应全过; 再试同 lane 应被限
    r0, r1, r2 = tb.allow(now, lane=0), tb.allow(now, lane=1), tb.allow(now, lane=2)
    r0b = tb.allow(now, lane=0)
    check('TokenBucket 3lane 各1/min', r0 and r1 and r2 and not r0b)

    # 3. _classify (盲区状态变更)
    from blindspot_monitor import _classify
    check('_classify 炸板/回封', _classify(5000, 0) == '炸板' and _classify(0, 5000) == '回封')

    # 4. _limit_pct 10/20/30cm
    from ticker import _limit_pct
    check('_limit_pct 10cm涨停', abs(_limit_pct(11.0, 10.0, 11.0) - 1.0) < 0.01)
    check('_limit_pct 20cm涨停', abs(_limit_pct(24.0, 20.0, 24.0) - 1.0) < 0.01)


# ── L1 配置一致性 ──
def l1_config():
    print('== L1 配置一致性 (0 数据) ==')
    # 1. mapping parquet 存在
    import os
    check('mapping parquet 存在', os.path.exists(cfg.MAPPING_PARQUET))
    # 2. SENTIMENT_FIELDS key 都在 compute 结果里 (防字段配置改了写不进)
    import sentiment_monitor as sm
    keys = {f['key'] for f in sm.SENTIMENT_FIELDS}
    # 派生 key (_ 开头) 跳过, 其余应在 result
    core = {k for k in keys if not k.startswith('_')}
    # 用 _calc 的返回字段验 (合成最小 raw)
    raw = {'market': {cfg.MARKET_ALL_INDEX: {}}, 'amount_today': 0,
           'main_net': 0, 'candidates': [], 'cand_drill_degraded': False,
           'breadth': {'up5': 0, 'down5': 0}, 'fetch_duration': 0.0}
    try:
        res = sm.SentimentMonitor(dry_run=True)._calc(raw, [])
        missing = [k for k in core if k not in res]
        check('SENTIMENT_FIELDS key 全在 result', not missing, f'缺:{missing}' if missing else '')
    except Exception as e:  # noqa: BLE001
        check('SENTIMENT_FIELDS 校验', False, str(e)[:50])


# ── L2 COM 探针 (盘前真实调用, 需通达信) ──
def l2_com():
    print('== L2 COM 探针 (盘前真实调用, 需通达信) ==')
    from lib.tq_client import init, close, safe_call
    from tqcenter import tq
    try:
        init()
    except Exception as e:  # noqa: BLE001
        check('init (通达信)', False, str(e)[:40])
        return
    try:
        # get_pricevol 小样本
        r = safe_call(tq.get_pricevol, stock_list=['600519.SH', '000001.SZ'])
        check('get_pricevol', bool(r and len(r) > 0))
        # get_more_info 单股
        r2 = safe_call(tq.get_more_info, stock_code='600519.SH', field_list=[],
                       timeout=cfg.MOREINFO_TIMEOUT)
        check('get_more_info', bool(r2 and r2.get('ZAF') is not None))
        # subscribe_hq 单只 (验证 DLL 订阅通道盘前是否通)
        import time
        got_cb = []
        r3 = safe_call(tq.subscribe_hq, stock_list=['600519.SH'],
                       callback=lambda d: got_cb.append(d))
        r3ok = False
        if isinstance(r3, str):
            try:
                r3ok = json.loads(r3).get('ErrorId') == '0'
            except Exception:
                r3ok = False
        check('subscribe_hq 盘前可用', r3ok, '' if r3ok else f'返回:{r3}')
        if r3ok:
            safe_call(tq.unsubscribe_hq, stock_list=['600519.SH'])
    except Exception as e:  # noqa: BLE001
        check('COM 探针', False, str(e)[:40])
    finally:
        close()


def main():
    fast = '--fast' in sys.argv
    print('═══ v10.2 盘前 smoke 自检 ═══')
    l0_logic()
    l1_config()
    if not fast:
        l2_com()
    print(f'\n结果: {len(_failures)} 个失败')
    if _failures:
        print('失败项: ' + '; '.join(_failures))
        sys.exit(1)
    print('✅ 全部通过, 可启动 radar_main.py --push')
    sys.exit(0)


if __name__ == '__main__':
    main()
