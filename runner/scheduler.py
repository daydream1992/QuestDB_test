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
  - H7: 子进程用 CREATE_NEW_PROCESS_GROUP 启动, 切换阶段时发 CTRL_BREAK_EVENT
    让子进程 main() 的 try/finally 触发, close() 关闭 tqcenter COM (避免泄漏 →
    通达信账号级互斥锁 "已有同名策略运行" 错误)。原 terminate() 在 Windows 上
    直接 TerminateProcess 不会运行 finally, tqcenter 句柄泄漏。
"""

import os
import sys
import time
import signal
import subprocess
import threading
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
_SUBSCRIBE_SCRIPT = os.path.join(_PROJ_ROOT, 'compute', 'subscribe.py')
_OVERSEER_SCRIPT = os.path.join(_PROJ_ROOT, 'runner', 'overseer.py')
# k4 深度情绪独立进程 (5min/轮, 由 scheduler 每 5min 启动一次)
_K4_RUNNER_SCRIPT = os.path.join(_PROJ_ROOT, 'runner', 'k4_runner.py')

# Python 解释器 (与当前进程一致)
_PY = sys.executable

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_scheduler_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# 主循环间隔 (秒)
_POLL_INTERVAL = 10
# 非交易日等待间隔 (秒)
_IDLE_INTERVAL = 300

MAX_CONSECUTIVE_CRASHES = 10

# 子进程崩溃统计 (用于 _ensure_running 指数退避)
_crash_count = {}

# H4: 退避线程化 — 不阻塞主循环
# {name: threading.Thread} 记录待执行的退避重启动作
_pending_backoff_threads: dict[str, threading.Thread] = {}
# 退避线程成功启动的新进程句柄 (主循环通过它拿到新进程)
_restarted_procs: dict[str, subprocess.Popen] = {}
# 哨兵: 退避线程已完成 (区别于 "无待处理线程" = None)
_BACKOFF_DONE = object()
# 过渡阶段禁止重启标记: should_prestart 主动停止某进程后,
# _ensure_running 不应立即将其重启 (解决 09:22-09:25 auction_monitor 反复起停)
_transition_stop: set[str] = set()


def _start_proc(script):
    """非阻塞启动一个 runner 子进程 (H7: CREATE_NEW_PROCESS_GROUP)

    必须加 CREATE_NEW_PROCESS_GROUP, 子进程才能作为独立进程组接收 CTRL_BREAK_EVENT
    (无此 flag 时, Windows CTRL_BREAK 会投到整个控制台, scheduler 自己也跟着死)。

    Args:
        script: 脚本绝对路径

    Returns:
        subprocess.Popen
    """
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([_PY, script], cwd=_PROJ_ROOT, **kwargs)
    logger.info('启动子进程: {} pid={}', os.path.basename(script), proc.pid)
    return proc


def _stop_proc(proc, name):
    """优雅停止子进程 (H7: CTRL_BREAK_EVENT → terminate → kill)

    流程:
      1. CTRL_BREAK_EVENT (Windows): 子进程 KeyboardInterrupt → main finally →
         tq.close() → 正常退出 (这是关键, 让 COM 句柄释放)
      2. 等 5s, 没退 → terminate() (TerminateProcess 兜底, 不走 finally)
      3. 再等 5s, 还没退 → kill() (Windows 杀进程组)

    Args:
        proc: subprocess.Popen, None 时直接返回
        name: 进程名 (日志用)
    """
    if proc is None:
        return
    if proc.poll() is not None:
        return  # 已退出
    # H7: 优先发 CTRL_BREAK (Windows), 让子进程 finally 跑
    if os.name == 'nt':
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            logger.info('已发 CTRL_BREAK 给 {} pid={}', name, proc.pid)
            try:
                proc.wait(timeout=5)
                logger.info('子进程优雅退出: {} pid={}', name, proc.pid)
                return
            except subprocess.TimeoutExpired:
                logger.warning('CTRL_BREAK 5s 未退出, 降级 terminate: {} pid={}', name, proc.pid)
        except (ValueError, OSError) as e:
            # 进程可能已经退出 / 权限问题 → 忽略走 terminate
            logger.debug('CTRL_BREAK 失败, 降级 terminate: {} ({})', name, e)
    else:
        # Linux/POSIX: 先发 SIGTERM (子进程 SIGTERM handler → KeyboardInterrupt → finally)
        try:
            proc.send_signal(signal.SIGTERM)
            logger.info('已发 SIGTERM 给 {} pid={}', name, proc.pid)
            try:
                proc.wait(timeout=5)
                logger.info('子进程优雅退出: {} pid={}', name, proc.pid)
                return
            except subprocess.TimeoutExpired:
                logger.warning('SIGTERM 5s 未退出, 降级 terminate: {} pid={}', name, proc.pid)
        except (ValueError, OSError) as e:
            logger.debug('SIGTERM 失败, 降级 terminate: {} ({})', name, e)
    # 兜底: terminate (TerminateProcess) → kill
    proc.terminate()
    try:
        proc.wait(timeout=5)
        logger.info('子进程已 terminate: {} pid={}', name, proc.pid)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        logger.warning('子进程 terminate 超时, 已 kill: {} pid={}', name, proc.pid)


def _ensure_running(proc, script, name, phase_ok):
    """确保子进程存活，已崩溃则在运行时段内自动重启（含指数退避）。

    H4: 退避等待在线程中执行，不阻塞 scheduler 主循环。
    """
    global _crash_count, _pending_backoff_threads, _restarted_procs

    # 过渡阶段主动停止的进程不重启 (see _transition_stop)
    if name in _transition_stop:
        return proc

    if not phase_ok:
        if proc is not None and proc.poll() is not None:
            logger.info('{} 子进程已退出 (非运行时段, code={})', name, proc.returncode)
            _crash_count.pop(name, None)
            _pending_backoff_threads.pop(name, None)
        return proc

    # 检查退避线程启动的新进程
    new_proc = _restarted_procs.pop(name, None)
    if new_proc is not None:
        logger.info('{} 收到退避线程启动的新进程 pid={}', name, new_proc.pid)
        _crash_count.pop(name, None)
        _pending_backoff_threads.pop(name, None)
        return new_proc

    # 检查是否有待执行的退避线程
    pending = _pending_backoff_threads.get(name)
    if pending is not None:
        if isinstance(pending, threading.Thread):
            if pending.is_alive():
                return proc
            else:
                _pending_backoff_threads.pop(name, None)
        # _BACKOFF_DONE: 清理
        _pending_backoff_threads.pop(name, None)

    if proc is None or proc.poll() is not None:
        if proc is not None:
            cnt = _crash_count.get(name, 0) + 1
            _crash_count[name] = cnt
            if cnt >= MAX_CONSECUTIVE_CRASHES:
                logger.critical('{} 子进程累计崩溃 {} 次, 停止重启', name, cnt)
                return proc
            backoff = min(60, 2 ** cnt)
            logger.error('{} 子进程已崩溃 (code={}), 第{}次, 等待{}s后重启 (线程化)',
                         name, proc.returncode, cnt, backoff)
            def _backoff_start(name, script):
                try:
                    time.sleep(backoff)
                    new_proc = _start_proc(script)
                    # 把新进程句柄存入全局，主循环下一次 _ensure_running 会读到
                    _restarted_procs[name] = new_proc
                except Exception as e:
                    logger.error('退避重启失败 {}: {}', name, e)
                finally:
                    _pending_backoff_threads[name] = _BACKOFF_DONE

            t = threading.Thread(target=_backoff_start, args=(name, script), daemon=True, name=f'backoff_{name}')
            t.start()
            _pending_backoff_threads[name] = t
            return proc  # 旧进程已崩溃，返回旧 proc 供下次判断
        else:
            logger.info('{} 子进程未启动, 启动', name)
            return _start_proc(script)

    # 进程正常运行 → 清掉崩溃计数
    _crash_count.pop(name, None)
    return proc


def _attach_if_running(script_name):
    """杀掉旧的 auction/intraday 子进程, 不杀 scheduler 本身 (H7: 优先 CTRL_BREAK)"""
    my_pid = os.getpid()
    script_basename = os.path.basename(script_name)
    killed = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == my_pid:
                continue  # 不杀 scheduler 本身
            cmdline = proc.info.get('cmdline') or []
            if any(str(c).endswith(script_basename) for c in cmdline if isinstance(c, str)):
                logger.warning('杀掉旧子进程 {} pid={}', script_basename, proc.pid)
                # H7: 优先 CTRL_BREAK (Windows), 让子进程 finally 跑 close()
                if os.name == 'nt':
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        pass
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    # 兜底: terminate (TerminateProcess) → kill
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        proc.kill()
                killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        time.sleep(1)  # 等待子进程真正退出
    return None


# PID 文件锁: 防止重复启动多个 scheduler
_LOCK_FILE = os.path.join(_PROJ_ROOT, 'logs', '.scheduler.lock')


def _is_scheduler_process(pid):
    """检查一个 PID 是否确实是 scheduler.py 进程 (防 PID 复用误判)"""
    try:
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
        return any('scheduler.py' in part for part in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
        return False


def _acquire_scheduler_lock():
    """进程锁: 基于 PID 文件 + 命令行验证, 防止重复启动 scheduler。

    逻辑:
      1. 读 .scheduler.lock 中的旧 PID
      2. 若旧 PID 存在且是 scheduler 进程 → 冲突, 退出
      3. 若旧 PID 是僵尸/被复用/无效 → 覆盖
      4. 写当前 PID 到锁文件
    """
    try:
        lock_fd = open(_LOCK_FILE, 'a+')
    except OSError as e:
        logger.error('scheduler 锁文件访问失败: {}', e)
        sys.exit(1)
    try:
        lock_fd.seek(0)
        old_content = lock_fd.read().strip()
        if old_content:
            try:
                old_pid = int(old_content)
                if old_pid != os.getpid() and _is_scheduler_process(old_pid):
                    logger.error('scheduler 已在运行 (PID={}), 退出', old_pid)
                    lock_fd.close()
                    sys.exit(1)
            except (ValueError, psutil.NoSuchProcess):
                pass  # 无效 PID 或僵尸, 覆盖
        lock_fd.seek(0)
        lock_fd.truncate()
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except OSError as e:
        lock_fd.close()
        logger.error('scheduler 锁文件访问失败: {}', e)
        sys.exit(1)


def _release_scheduler_lock(lock_fd):
    """释放锁文件"""
    try:
        lock_fd.close()
        os.remove(_LOCK_FILE)
    except Exception:
        pass


def run():
    """总调度主循环

    流程:
      1. 进程锁检查 (防止重复启动)
      2. 加载策略配置 (load_plugins + load_config)
      3. while True 轮询 market_clock.get_phase():
         - auction:    启动 auction_monitor 子进程 (非阻塞)
         - pre_market: 直接调 daily_init.run() (仅一次)
         - morning/afternoon: 停 auction 子进程, 启动 intraday_loop 子进程
         - closed:     停 intraday 子进程, 直接调 daily_close.run() (仅一次)
      4. Ctrl+C 优雅退出 (终止所有子进程)
      5. 非交易日跳过, 仅补跑 daily_close 后等待次日
    """
    lock_fd = _acquire_scheduler_lock()
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
    from lib.tq_client import init, close as tq_close
    from lib.market_clock import (
        seconds_until, should_prestart, countdown_seconds, format_countdown
    )

    # scheduler 进程自身也需 tqcenter init (因直接调 daily_init.run/daily_close.run)
    init()

    # 阶段完成标记 (避免重复执行)
    flags = {'daily_init': False, 'daily_close': False, 'verify_tables': False}
    # 阻塞型子进程句柄
    auction_proc = None
    intraday_proc = None
    subscribe_proc = None
    overseer_proc = None
    k4_runner_proc = None
    # k4 5min 节流: 记录上次启动时间, 不依赖 round_idx
    _last_k4_run = None

    # 启动时杀掉已运行的旧子进程，防止重复拉起
    _attach_if_running('auction_monitor')
    _attach_if_running('intraday_loop')
    _attach_if_running('subscribe')
    _attach_if_running('overseer')
    _attach_if_running('k4_runner')

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

            # === 提前启动检查 (在阶段切换前预热子进程) ===
            # 用 should_prestart 提前 8 分钟启动, 让进程在阶段到来前完成初始化

            # 竞价: 目标 09:15, 提前 8 分钟 → 约 09:07 启动
            if should_prestart(9, 15):
                if auction_proc is None:
                    logger.info('提前启动 auction_monitor (距竞价约 %s)', format_countdown(9,15))
                    auction_proc = _start_proc(_AUCTION_SCRIPT)

            # 开盘: 目标 09:30, 提前 8 分钟 → 约 09:22 停竞价 + 启动 intraday
            if should_prestart(9, 30):
                if auction_proc is not None:
                    logger.info('停 auction_monitor, 准备切换到盘中')
                    _transition_stop.add('auction_monitor')
                    _stop_proc(auction_proc, 'auction_monitor')
                    auction_proc = None
                if intraday_proc is None:
                    logger.info('提前启动 intraday_loop (距开盘约 %s)', format_countdown(9,30))
                    intraday_proc = _start_proc(_INTRADAY_SCRIPT)
                if subscribe_proc is None:
                    subscribe_proc = _start_proc(_SUBSCRIBE_SCRIPT)
                if overseer_proc is None:
                    overseer_proc = _start_proc(_OVERSEER_SCRIPT)

            # 下午盘: 目标 13:00, 提前 8 分钟 → 约 12:52 启动
            if should_prestart(13, 0):
                if intraday_proc is None:
                    logger.info('提前启动 intraday_loop (距下午盘约 %s)', format_countdown(13,0))
                    intraday_proc = _start_proc(_INTRADAY_SCRIPT)
                if subscribe_proc is None:
                    subscribe_proc = _start_proc(_SUBSCRIBE_SCRIPT)
                if overseer_proc is None:
                    overseer_proc = _start_proc(_OVERSEER_SCRIPT)

            # 下午收盘竞价: 目标 14:57, 提前 8 分钟 → 约 14:49 启动
            if should_prestart(14, 57):
                if auction_proc is None:
                    logger.info('提前启动 auction_monitor (距收盘竞价约 %s)', format_countdown(14,57))
                    auction_proc = _start_proc(_AUCTION_SCRIPT)

            # k4 深度情绪: 盘中时段 (morning/afternoon/lunch/pre_close) 每 5 分钟启动一次
            # 用墙钟节流, 不依赖 round_idx, 跨重启自动重置
            from datetime import timedelta
            in_intraday = phase in ('morning', 'afternoon', 'lunch', 'pre_close')
            if in_intraday:
                need_run = (
                    _last_k4_run is None
                    or (now - _last_k4_run) >= timedelta(minutes=5)
                )
                if need_run:
                    should_start = True
                    # 不管上一次 k4_runner 是否还活着, 都无条件拉新进程
                    # 5min 节流只靠 _last_k4_run (上次启动时间), 与进程存活无关
                    # 如果上次进程还在, 自然结束 (k4_runner 跑完就退, 不会卡死)
                    if k4_runner_proc is not None:
                        try:
                            if k4_runner_proc.poll() is None:
                                # 上次进程还在跑 (异常), 跳过本轮启动, 但不下 continue — 其余进程仍需健康检查
                                logger.warning('k4_runner 上次进程仍存活 (pid={}), 跳过本轮', k4_runner_proc.pid)
                                should_start = False
                        except Exception:
                            pass
                    if should_start:
                        logger.info('启动 k4_runner (距上次 {} 分钟)',
                                    '?' if _last_k4_run is None else f'{(now - _last_k4_run).total_seconds() / 60:.1f}')
                        k4_runner_proc = _start_proc(_K4_RUNNER_SCRIPT)
                        _last_k4_run = now
            else:
                # 非盘中时段: 停 k4_runner (如有)
                if k4_runner_proc is not None and k4_runner_proc.poll() is None:
                    _stop_proc(k4_runner_proc, 'k4_runner')
                    logger.info('非盘中时段, 停止 k4_runner')
                k4_runner_proc = None

            # === 原有的 get_phase() 逻辑 (兜底 + 一次性任务) ===
            if phase == 'pre_market':
                # 过渡期已过 (auction → pre_market), 清除禁止重启标记
                _transition_stop.discard('auction_monitor')
                # 09:25-09:30 撮合段: 跑 daily_init (一次性)
                if not flags['daily_init'] and now.hour == 9 and now.minute >= 25:
                    try:
                        daily_init.run()
                    except Exception as e:
                        logger.error('daily_init 失败: {}', e)
                    flags['daily_init'] = True

            elif phase == 'closed':
                # 收盘后: 停 intraday + subscribe + overseer
                if intraday_proc is not None:
                    _stop_proc(intraday_proc, 'intraday_loop')
                    intraday_proc = None
                if subscribe_proc is not None:
                    _stop_proc(subscribe_proc, 'subscribe')
                    subscribe_proc = None
                if overseer_proc is not None:
                    _stop_proc(overseer_proc, 'overseer')
                    overseer_proc = None
                if not flags['daily_close']:
                    try:
                        daily_close.run()
                        try:
                            import runner.daily_summary as summary
                            summary.run()
                        except Exception as e:
                            logger.error('复盘汇总失败: {}', e)
                        flags['daily_close'] = True
                    except Exception as e:
                        logger.error('daily_close 失败, 将重试: {}', e)
                        # 不置 flags, 下次循环重试
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

            # 存活检查: 子进程在运行时段内崩溃则自动重启
            # (should_prestart 只在 8 分钟窗口内启动，过后崩溃无人拉起)
            in_auction = phase in ('auction',)
            in_intraday = phase in ('morning', 'afternoon', 'lunch', 'pre_close')
            if in_intraday:
                # 盘中目标阶段已到, 清除过渡期标记; 若再需要 auction_monitor,
                # 由 should_prestart(14,57) 正常启动
                _transition_stop.discard('auction_monitor')

            auction_proc = _ensure_running(
                auction_proc, _AUCTION_SCRIPT, 'auction_monitor', in_auction)
            intraday_proc = _ensure_running(
                intraday_proc, _INTRADAY_SCRIPT, 'intraday_loop', in_intraday)
            subscribe_proc = _ensure_running(
                subscribe_proc, _SUBSCRIBE_SCRIPT, 'subscribe', in_intraday)
            overseer_proc = _ensure_running(
                overseer_proc, _OVERSEER_SCRIPT, 'overseer', in_intraday)

            time.sleep(_POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info('Ctrl+C 退出 scheduler')
    finally:
        _stop_proc(auction_proc, 'auction_monitor')
        _stop_proc(intraday_proc, 'intraday_loop')
        _stop_proc(subscribe_proc, 'subscribe')
        _stop_proc(overseer_proc, 'overseer')
        _stop_proc(k4_runner_proc, 'k4_runner')
        try:
            tq_close()
        except Exception:
            pass
        _release_scheduler_lock(lock_fd)
        logger.info('===== scheduler 退出 =====')


def main():
    import signal

    def _graceful_exit(signum, frame):
        raise KeyboardInterrupt

    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    run()


if __name__ == '__main__':
    main()
