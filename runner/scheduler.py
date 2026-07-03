"""总调度器 (全天调度)

脚本路径: K:\QuestDB_test\\runner\\scheduler.py
用途: 全天自动调度, 按时间段启动不同模块
调度时序:
  09:15-09:30  auction_monitor (竞价监控)
  09:25        daily_init (盘前初始化, 与竞价并行)
  09:30-15:00  intraday_loop (盘中主循环)
  15:05        daily_close (盘后更新)
  16:00        verify_tables (DDL 表结构校验, 防列名漂移)
执行: python runner/scheduler.py

说明:
  - auction_monitor / intraday_loop 的 run() 内部是 while True 阻塞循环
    (退出条件: 非交易日 或 15:00 后), 直接调用会阻塞 scheduler 主循环,
    导致无法在 09:30 从竞价切换到盘中。因此这两个阻塞型 runner 用
    subprocess.Popen 非阻塞启动, 阶段切换时 terminate 上一阶段子进程。
  - daily_init / daily_close 是一次性执行 (非阻塞), 直接在 scheduler 进程内调用。
  - 每个阶段完成后标记 done, 避免重复执行。
  - 非交易日跳过 (仅补跑 daily_close 后等待次日)。
  - 模块级 import 不引入 tqcenter 依赖, 确保 `from runner.scheduler import run`
    可在无 tqcenter 环境下 import 成功。
"""

import os
import sys
import time
import subprocess
import psutil
from datetime import datetime, time as dtime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

# 以下两个模块不依赖 tqcenter, 可安全在模块级 import
from lib.market_clock import get_phase, is_trading_day  # noqa: E402
from strategy.registry import StrategyRegistry  # noqa: E402

# 配置路径
_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')
_PLUGINS_DIR = os.path.join(_PROJ_ROOT, 'strategy', 'plugins')

# 阻塞型 runner 脚本路径 (用 subprocess 启动)
_AUCTION_SCRIPT = os.path.join(_PROJ_ROOT, 'runner', 'auction_monitor.py')
_INTRADAY_SCRIPT = os.path.join(_PROJ_ROOT, 'runner', 'intraday_loop.py')

# Python 解释器 (与当前进程一致)
_PY = sys.executable

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_scheduler_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# 主循环间隔 (秒)
_POLL_INTERVAL = 30
# 非交易日等待间隔 (秒)
_IDLE_INTERVAL = 300


def _start_proc(script):
    """非阻塞启动一个 runner 子进程

    Args:
        script: 脚本绝对路径

    Returns:
        subprocess.Popen
    """
    proc = subprocess.Popen([_PY, script], cwd=_PROJ_ROOT)
    logger.info('启动子进程: {} pid={}', os.path.basename(script), proc.pid)
    return proc


def _stop_proc(proc, name):
    """优雅停止子进程 (terminate → wait 10s → kill)

    Args:
        proc: subprocess.Popen, None 时直接返回
        name: 进程名 (日志用)
    """
    if proc is None:
        return
    if proc.poll() is None:  # 仍在运行
        proc.terminate()
        try:
            proc.wait(timeout=10)
            logger.info('子进程已终止: {} pid={}', name, proc.pid)
        except subprocess.TimeoutExpired:
            proc.kill()
            logger.warning('子进程 terminate 超时, 已 kill: {} pid={}', name, proc.pid)


def _attach_if_running(script_name):
    """杀掉旧的 auction/intraday 子进程, 不杀 scheduler 本身"""
    my_pid = os.getpid()
    script_basename = os.path.basename(script_name)
    killed = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == my_pid:
                continue  # 不杀 scheduler 本身
            cmdline = proc.info.get('cmdline') or []
            if any(script_basename in str(c) for c in cmdline):
                logger.warning('杀掉旧子进程 {} pid={}', script_basename, proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        time.sleep(1)  # 等待子进程真正退出
    return None


def run():
    """总调度主循环

    流程:
      1. 加载策略配置 (load_plugins + load_config)
      2. while True 轮询 market_clock.get_phase():
         - auction:    启动 auction_monitor 子进程 (非阻塞)
         - pre_market: 直接调 daily_init.run() (仅一次)
         - morning/afternoon: 停 auction 子进程, 启动 intraday_loop 子进程
         - closed:     停 intraday 子进程, 直接调 daily_close.run() (仅一次)
      3. Ctrl+C 优雅退出 (终止所有子进程)
      4. 非交易日跳过, 仅补跑 daily_close 后等待次日
    """
    logger.info('===== scheduler 启动 {} =====', datetime.now())

    # 1. 加载策略配置
    try:
        StrategyRegistry.load_plugins(_PLUGINS_DIR)
        StrategyRegistry.load_config(_YAML_PATH)
        logger.info('策略加载完成: 启用 {} 个', len(StrategyRegistry.get_all()))
    except Exception as e:
        logger.error('策略配置加载失败: {}', e)

    # 延迟 import: daily_init / daily_close 依赖 tqcenter, 放到 run() 内部
    # 避免模块级 import 触发 tqcenter 初始化, 保证 `from runner.scheduler import run` 可用
    import runner.daily_init as daily_init
    import runner.daily_close as daily_close

    # 阶段完成标记 (避免重复执行)
    flags = {'daily_init': False, 'daily_close': False, 'verify_tables': False}
    # 阻塞型子进程句柄
    auction_proc = None
    intraday_proc = None

    # 启动时杀掉已运行的旧子进程，防止重复拉起
    _attach_if_running('auction_monitor')
    _attach_if_running('intraday_loop')

    try:
        while True:
            now = datetime.now()
            phase = get_phase(now)

            # === 非交易日 ===
            if not is_trading_day(now):
                # 若已盘前初始化但未盘后更新, 补跑 daily_close
                if flags['daily_init'] and not flags['daily_close']:
                    _stop_proc(auction_proc, 'auction_monitor')
                    auction_proc = None
                    _stop_proc(intraday_proc, 'intraday_loop')
                    intraday_proc = None
                    try:
                        daily_close.run()
                    except Exception as e:
                        logger.error('daily_close 失败: {}', e)
                    flags['daily_close'] = True
                logger.info('非交易日, 等待... (phase={})', phase)
                time.sleep(_IDLE_INTERVAL)
                continue

            # === 交易日 ===
            logger.debug('phase={}, auction_proc={}, intraday_proc={}, flags={}',
                         phase, auction_proc, intraday_proc, flags)
            if phase == 'auction':
                # 竞价阶段: 启动 auction_monitor (子进程, 非阻塞)
                if auction_proc is None:
                    auction_proc = _start_proc(_AUCTION_SCRIPT)

            elif phase == 'pre_market':
                # 09:25-09:30 撮合段: 跑 daily_init (一次性, 直接调用)
                if not flags['daily_init']:
                    try:
                        daily_init.run()
                    except Exception as e:
                        logger.error('daily_init 失败: {}', e)
                    # 无论成功失败都标记, 避免反复重试
                    flags['daily_init'] = True

            elif phase in ('morning', 'afternoon'):
                # 盘中阶段: 切换竞价 → 盘中
                logger.info('检测到 morning/afternoon 阶段, 切换到 intraday_loop')
                if auction_proc is not None:
                    _stop_proc(auction_proc, 'auction_monitor')
                    auction_proc = None
                if intraday_proc is None:
                    intraday_proc = _start_proc(_INTRADAY_SCRIPT)

            elif phase == 'closed':
                # 收盘后: 停 intraday, 跑 daily_close (一次性)
                if intraday_proc is not None:
                    _stop_proc(intraday_proc, 'intraday_loop')
                    intraday_proc = None
                if not flags['daily_close']:
                    try:
                        daily_close.run()
                    except Exception as e:
                        logger.error('daily_close 失败: {}', e)
                    flags['daily_close'] = True
                # 16:00 后跑 DDL 表结构校验 (一次性, 防列名漂移; 不通过仅日志告警不阻断)
                if now.time() >= dtime(16, 0) and not flags['verify_tables']:
                    try:
                        import subprocess
                        script = os.path.join(_PROJ_ROOT, 'scripts', 'verify_tables.py')
                        r = subprocess.run([_PY, script], cwd=_PROJ_ROOT,
                                           capture_output=True, text=True,
                                           encoding='utf-8', timeout=60)
                        # 把 verify_tables 输出原样打到 scheduler 日志
                        if r.stdout:
                            for line in r.stdout.strip().splitlines():
                                logger.info('verify_tables | {}', line)
                        if r.returncode != 0:
                            logger.warning('verify_tables 退出码 {} (有 ❌ 或异常)', r.returncode)
                        else:
                            logger.info('verify_tables: 全部对齐 ✅')
                    except Exception as e:
                        logger.warning('verify_tables 调用失败: {}', e)
                    flags['verify_tables'] = True
                # 当日全流程完成, 等待次日
                logger.info('当日调度完成, 等待次日...')
                time.sleep(_IDLE_INTERVAL)
                continue

            # lunch / pre_close 阶段: intraday 子进程自行处理, scheduler 不操作
            time.sleep(_POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info('Ctrl+C 退出 scheduler')
    finally:
        _stop_proc(auction_proc, 'auction_monitor')
        _stop_proc(intraday_proc, 'intraday_loop')
        logger.info('===== scheduler 退出 =====')


def main():
    run()


if __name__ == '__main__':
    main()
