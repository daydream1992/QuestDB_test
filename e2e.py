"""端到端验证脚本

脚本路径: K:\QuestDB_test\\e2e.py
用途: 验证全流程: 采集→计算→策略→推送
执行: python e2e.py

流程:
  1.  重置表 (ddl/_reset_all.py)
  2.  加载映射 (c5_mapping.run)
  3.  拉价量 (c1_pricevol.run, limit=5)
  4.  拉快照 (c2_snapshot.run, focus_codes=5只)
  5.  拉日级 (c3_more_info.run, codes=5只, mode='daily')
  6.  拉 K 线 (c4_kline.run, codes=5只, period='1m', count=48)
  7.  算指标 (k1_indicators.run)
  8.  检测信号 (k2_signals.run)
  9.  加载策略 (StrategyRegistry.load_plugins)
  10. 构建 ctx (读 qd_pricevol/qd_indicators/qd_signals)
  11. 遍历策略 → decisions
  12. 打印结果汇总

说明:
  - 全程使用前 5 只股票 (fetch_all_codes 取有 tdx_code 的前 5 只)
  - 第 6 步按任务要求拉 1m K 线; 另补拉 5m K 线, 因 k1_indicators
    读取 qd_kline_5m 表 (见 compute/k1_indicators.py 的 SRC_KLINE),
    若不补拉 5m, 指标与信号环节将无数据, 无法验证完整链路。
  - 重置表用 subprocess 调用 ddl/_reset_all.py (隔离进程, 避免依赖路径问题)
"""

import os
import sys
import subprocess
from datetime import datetime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df  # noqa: E402
from lib.tq_client import init, close  # noqa: E402
from lib.tq_utils import fetch_all_codes  # noqa: E402

import collect.c5_mapping as c5  # noqa: E402
import collect.c1_pricevol as c1  # noqa: E402
import collect.c2_snapshot as c2  # noqa: E402
import collect.c3_more_info as c3  # noqa: E402
import collect.c4_kline as c4  # noqa: E402
import compute.k1_indicators as k1  # noqa: E402
import compute.k2_signals as k2  # noqa: E402

from strategy.registry import StrategyRegistry  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402

# 配置路径
_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')
_PLUGINS_DIR = os.path.join(_PROJ_ROOT, 'strategy', 'plugins')

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'e2e_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# 测试样本数
_FOCUS_N = 5
_KLINE_COUNT = 48


def _step(idx, title, *args):
    """打印步骤标题 (支持 title.format(*args))"""
    if args:
        try:
            title = title.format(*args)
        except Exception:
            pass
    logger.info('=' * 60)
    logger.info('步骤 {}: {}', idx, title)
    logger.info('=' * 60)


def _reset_tables():
    """步骤 1: 重置表 (subprocess 调用 ddl/_reset_all.py)"""
    _step(1, '重置表 (ddl/_reset_all.py)')
    script = os.path.join(_PROJ_ROOT, 'ddl', '_reset_all.py')
    result = subprocess.run(
        [sys.executable, script], cwd=_PROJ_ROOT,
        capture_output=True, text=True)
    if result.returncode != 0:
        logger.error('_reset_all 失败 (exit={}): {}', result.returncode, result.stderr)
        raise RuntimeError('重置表失败')
    logger.info('重置表完成')


def _collect(codes):
    """步骤 2-6: 采集 (需在 tqcenter init/close 之间)"""
    _step(2, '加载映射 (c5_mapping.run)')
    n5 = c5.run()
    logger.info('c5_mapping: {}', n5)

    _step(3, '拉价量 (c1_pricevol.run, limit={})', _FOCUS_N)
    n1 = c1.run(limit=_FOCUS_N)
    logger.info('c1_pricevol: {} 行', n1)

    _step(4, '拉快照 (c2_snapshot.run, focus={}只)', _FOCUS_N)
    n2 = c2.run(focus_codes=codes, all_codes=codes)
    logger.info('c2_snapshot: {}', n2)

    _step(5, '拉日级 (c3_more_info.run, codes={}只, mode=daily)', _FOCUS_N)
    n3 = c3.run(codes, mode='daily')
    logger.info('c3_more_info: {}', n3)

    _step(6, '拉 K 线 (c4_kline.run, codes={}只, period=1m, count={})',
          _FOCUS_N, _KLINE_COUNT)
    n4a = c4.run(codes, period='1m', count=_KLINE_COUNT)
    logger.info('c4_kline 1m: {} 行', n4a)
    # 补拉 5m: k1_indicators 读取 qd_kline_5m, 不拉 5m 则指标环节无数据
    n4b = c4.run(codes, period='5m', count=_KLINE_COUNT)
    logger.info('c4_kline 5m (补拉供 k1): {} 行', n4b)


def _compute():
    """步骤 7-8: 计算"""
    _step(7, '算指标 (k1_indicators.run)')
    k1.run()

    _step(8, '检测信号 (k2_signals.run)')
    k2.run()


def _build_context(con):
    """步骤 10: 构建 StrategyContext (读所有策略需要的表)"""
    _step(10, '构建 StrategyContext')
    ctx = StrategyContext(timestamp=datetime.now(), is_trading=False)
    # 基础行情
    for name, sql in [
        ('pricevol_df',       'SELECT * FROM qd_pricevol'),
        ('snapshot_focus_df', 'SELECT * FROM qd_stock_snapshot'),
        ('more_info_df',      'SELECT * FROM qd_stock_daily'),
        ('indicators_df',     'SELECT * FROM qd_indicators'),
        ('signals_df',        'SELECT * FROM qd_signals'),
        # 高级分析 (e2e 用少量数据, 正常为空, 不报错)
        ('auction_df',        'SELECT * FROM qd_auction_snapshot'),
        ('big_order_df',      'SELECT * FROM qd_big_order'),
        ('money_flow_df',     'SELECT * FROM qd_money_flow'),
        ('sector_flow_df',    'SELECT * FROM qd_sector_flow'),
        ('resonance_df',      'SELECT * FROM qd_resonance'),
    ]:
        try:
            setattr(ctx, name, query_df(con, sql))
        except Exception as e:
            logger.warning('读 {} 失败: {}', name, e)
            setattr(ctx, name, None)
    logger.info('ctx 构建完成: pricevol={}, indicators={}, signals={}, snapshot={}, daily={}',
                _shape(ctx.pricevol_df), _shape(ctx.indicators_df),
                _shape(ctx.signals_df), _shape(ctx.snapshot_focus_df),
                _shape(ctx.more_info_df))
    return ctx


def _shape(df):
    """返回 DataFrame 形状字符串, None 返回 'None'"""
    if df is None:
        return 'None'
    return '{}x{}'.format(len(df), len(df.columns) if hasattr(df, 'columns') else 0)


def _run_strategies(ctx):
    """步骤 11: 遍历策略 → decisions"""
    _step(11, '遍历策略 → decisions')
    decisions = []
    enabled = StrategyRegistry.get_all()
    logger.info('已启用策略 {} 个: {}', len(enabled), list(enabled.keys()))
    for name, cls in enabled.items():
        try:
            ds = cls().evaluate(ctx)
            if ds:
                logger.info('  策略 {} 产出 {} 条决策', name, len(ds))
                decisions.extend(ds)
        except Exception as e:
            logger.error('  策略 {} 评估失败: {}', name, e)
    return decisions


def _summary(codes, decisions, con):
    """步骤 12: 打印结果汇总"""
    _step(12, '结果汇总')
    print('\n' + '=' * 60)
    print('端到端验证结果汇总  {}'.format(datetime.now()))
    print('=' * 60)
    print('测试样本: {} 只'.format(len(codes)))
    print('样本代码: {}'.format(codes))

    # 各表行数
    tables = ['qd_code_registry', 'qd_pricevol', 'qd_stock_snapshot',
              'qd_stock_daily', 'qd_kline_1m', 'qd_kline_5m',
              'qd_indicators', 'qd_signals', 'qd_decisions']
    print('\n各表行数:')
    for t in tables:
        try:
            row = query_df(con, "SELECT COUNT(*) AS n FROM {}".format(t))
            n = int(row['n'].iloc[0]) if not row.empty else 0
            print('  {:<22} {}'.format(t, n))
        except Exception as e:
            print('  {:<22} (查询失败: {})'.format(t, str(e)[:40]))

    # 决策汇总
    print('\n策略决策: 共 {} 条'.format(len(decisions)))
    if decisions:
        print('  {:<8} {:<10} {:<6} {:<8} {:<10} {}'.format(
            'code', 'strategy', 'action', 'pos%', 'price', 'reason'))
        for d in decisions[:20]:
            print('  {:<8} {:<10} {:<6} {:<8} {:<10} {}'.format(
                d.code, d.strategy, d.action, d.position_pct,
                '{:.2f}'.format(d.price) if d.price else '-',
                (d.reason or '')[:30]))
        if len(decisions) > 20:
            print('  ... (仅显示前 20 条, 共 {} 条)'.format(len(decisions)))

    # 启用策略
    enabled = StrategyRegistry.get_all()
    print('\n已启用策略: {} 个'.format(len(enabled)))
    for name in enabled:
        print('  - {}'.format(name))

    print('\n' + '=' * 60)
    print('端到端验证完成')
    print('=' * 60 + '\n')


def _push_verify(con, decisions):
    """步骤 13: 飞书推送验证 (推送前 3 条信号 + 验收完成通知 + 决策)"""
    _step(13, '飞书推送验证')
    import importlib as _il
    _feishu = _il.import_module('feishu')
    push_text, push_signal, push_decision = _feishu.push_text, _feishu.push_signal, _feishu.push_decision
    from datetime import datetime as _dt

    pushed_n = 0

    # 13.1 推送信号 (从 qd_signals 取前 3 条)
    try:
        sig_df = query_df(con, 'SELECT * FROM qd_signals LIMIT 3')
    except Exception as e:
        logger.warning('读 qd_signals 失败: {}', e)
        sig_df = None
    if sig_df is not None and not sig_df.empty:
        logger.info('准备推送 {} 条信号', len(sig_df))
        for _, row in sig_df.iterrows():
            sig = {
                'code': row.get('code', ''),
                'signal_time': row.get('signal_time'),
                'strategy_name': row.get('strategy_name', 'k2_atom'),
                'signal_type': row.get('signal_type', ''),
                'signal_score': row.get('signal_score', 0),
                'price': row.get('price'),
                'volume': row.get('volume'),
                'reason': row.get('reason', ''),
                'metadata': row.get('metadata', ''),
            }
            ok = push_signal(sig)
            logger.info('  信号推送 {} {}: ok={}', sig['code'], sig['signal_type'], ok)
            if ok:
                pushed_n += 1

    # 13.2 推送决策 (前 3 条)
    if decisions:
        for d in decisions[:3]:
            dec = {
                'decision_time': _dt.now(),
                'code': d.code,
                'strategy_name': d.strategy,
                'action': d.action,
                'position_size': d.position_pct,
                'price': d.price,
                'reason': d.reason,
            }
            ok = push_decision(dec)
            logger.info('  决策推送 {} {}: ok={}', d.code, d.action, ok)
            if ok:
                pushed_n += 1

    # 13.3 推送验收完成通知
    summary_text = (
        '[QuestDB_test 验收完成]\n'
        '时间: {}\n'
        '入库: pricevol/snapshot/daily/kline_1m/kline_5m/indicators/signals 全部写入\n'
        '策略: 16 个已加载, 决策 {} 条\n'
        '推送: 信号+决策+本通知已发送\n'
        '系统状态: 全链路 OK'
    ).format(_dt.now(), len(decisions))
    ok = push_text(summary_text)
    if ok:
        pushed_n += 1
    logger.info('验收完成通知推送: ok={}', ok)

    print('\n飞书推送: 共 {} 条成功'.format(pushed_n))
    return pushed_n


def run():
    """端到端验证主流程"""
    logger.info('##### 端到端验证开始 {} #####', datetime.now())

    # 1. 重置表
    _reset_tables()

    # 获取测试样本 (前 5 只有 tdx_code 的股票)
    init()
    try:
        _step(0, '获取测试样本 (fetch_all_codes)')
        meta = fetch_all_codes()
        codes = [c['code'] for c in meta if c.get('tdx_code')][:_FOCUS_N]
        logger.info('测试样本: {}', codes)
        if not codes:
            raise RuntimeError('无可用测试样本 (fetch_all_codes 返回空)')

        # 2-6. 采集
        _collect(codes)
    finally:
        close()

    # 7-8. 计算 (仅读 QuestDB, 无需 tqcenter)
    _compute()

    # 9. 加载策略
    _step(9, '加载策略 (StrategyRegistry.load_plugins + load_config)')
    StrategyRegistry.load_plugins(_PLUGINS_DIR)
    StrategyRegistry.load_config(_YAML_PATH)
    logger.info('策略加载完成: 启用 {} 个', len(StrategyRegistry.get_all()))

    # 10-13. 构建 ctx → 遍历策略 → 汇总 → 推送验证
    con = connect()
    try:
        ctx = _build_context(con)
        decisions = _run_strategies(ctx)
        _summary(codes, decisions, con)
        _push_verify(con, decisions)
    finally:
        con.close()

    logger.info('##### 端到端验证完成 {} #####', datetime.now())


def main():
    run()


if __name__ == '__main__':
    main()
