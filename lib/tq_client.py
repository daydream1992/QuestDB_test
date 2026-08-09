"""tqcenter 客户端封装

脚本路径: K:/QuestDB_test//lib//tq_client.py
用途: 封装 tqcenter COM 调用, 提供线程安全的 init/close/retry
依赖: tqcenter (K:\\txdlianghua\\PYPlugins\\sys\\tqcenter.py)
数据源: tqcenter (通达信量化数据接口)
入库表: 无 (本模块仅提供调用包装, 入库由调用方完成)
说明:
  - tqcenter 是 C++ COM 组件, 不支持多线程并发
  - 多进程安全 (各自独立 init/close)
  - 单进程内用 threading.Lock 保证串行
  - 失败自动重试 3 次, 每次重试前 close + initialize
"""

import os
import sys
import threading
import functools
import time as _time

from dotenv import load_dotenv
from loguru import logger

# 加载 config/.env
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', '.env')
load_dotenv(_ENV_PATH)

TQCENTER_PATH = os.getenv('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys')

# 把 tqcenter 路径加入 sys.path, 以便 import tq
if TQCENTER_PATH and TQCENTER_PATH not in sys.path:
    sys.path.insert(0, TQCENTER_PATH)

from tqcenter import tq  # noqa: E402  (tqcenter 的 tq 类)

# 模块级锁: tqcenter 不支持多线程并发, 单进程内串行
_lock = threading.Lock()

# 标记当前进程是否已 initialize
_initialized = False

# 重试退避配置
_RETRY_BASE_DELAY = 0.1   # 100ms
_RETRY_MAX_DELAY = 5.0    # 5s (H1: 提升避免高频重试打满 tqcenter)


def init(path=None):
    """初始化 tqcenter 连接

    Args:
        path: tqcenter 所在目录, 默认从 .env 的 TQCENTER_PATH 读取
    """
    global _initialized
    with _lock:
        if _initialized:
            logger.debug('tqcenter 已初始化, 跳过重复 init')
            return
        if path is None:
            path = TQCENTER_PATH
        tq.initialize(path)
        _initialized = True


def close():
    """关闭 tqcenter 连接"""
    global _initialized
    with _lock:
        if _initialized:
            try:
                tq.close()
            except Exception:
                pass
            _initialized = False


def _ensure_init():
    """(内部) 确保已初始化, 调用方需已持有 _lock"""
    global _initialized
    if not _initialized:
        tq.initialize(TQCENTER_PATH)
        _initialized = True


def _reconnect():
    """(内部) close + initialize 重连, 调用方需已持有 _lock"""
    global _initialized
    try:
        tq.close()
    except Exception:
        pass
    _initialized = False
    try:
        tq.initialize(TQCENTER_PATH)
        _initialized = True
        logger.warning('tqcenter 重连成功 ({})', TQCENTER_PATH)
    except Exception as e:  # noqa: BLE001
        _initialized = False
        logger.warning('tqcenter 重连失败: {}', e)


def retry(func):
    """重试装饰器: 失败自动重试 3 次, 每次重试前 close + initialize

    用于包装 tqcenter 的方法调用, 保证线程安全 + 自动重连。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for _ in range(3):
            with _lock:
                _ensure_init()
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    _reconnect()
        raise last_exc
    return wrapper


def safe_call(func, *args, timeout=None, **kwargs):
    """通用调用包装: 线程安全 + 自动重试 3 次 + 指数退避 + 可选单调用超时

    用法: safe_call(tq.get_sector_list, list_type=0)
          safe_call(tq.get_more_info, stock_code=code, field_list=[], timeout=8)

    Args:
        func: tqcenter 的方法 (如 tq.get_sector_list)
        timeout: 单次调用超时 (秒, 默认 None=不超时)。COM 卡死时用线程 + join
                 超时兜底 (僵尸线程残留, 但主循环不被拖垮), 复活 MOREINFO_TIMEOUT。
        *args, **kwargs: 传给 func 的参数

    Returns:
        func 的返回值; 超时返回 None

    Raises:
        最后一次重试仍失败时抛出异常 (非超时场景)
    """
    last_exc = None
    for attempt in range(3):
        if not _lock.acquire(timeout=30):
            raise TimeoutError('tqcenter safe_call 等待锁超时 (30s)')
        try:
            _ensure_init()
            try:
                if timeout is not None:
                    # 线程 + join 超时: 防单次 COM 卡死无限阻塞 (v10.1 _with_timeout 模式)
                    result = [None]
                    exc = [None]
                    def _do():
                        try:
                            result[0] = func(*args, **kwargs)
                        except Exception as e:  # noqa: BLE001
                            exc[0] = e
                    t = threading.Thread(target=_do, daemon=True)
                    t.start()
                    t.join(timeout=timeout)
                    if t.is_alive():
                        logger.warning('{} 超时 {:.0f}s (COM 可能卡死, 僵尸线程残留)',
                                      getattr(func, '__name__', 'tq'), timeout)
                        return None
                    if exc[0] is not None:
                        last_exc = exc[0]
                        raise exc[0]
                    return result[0]
                return func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                _reconnect()
                if attempt < 2:  # 不是最后一次重试，添加退避
                    delay = min(_RETRY_BASE_DELAY * (2 ** attempt), _RETRY_MAX_DELAY)
                    _time.sleep(delay)
        finally:
            _lock.release()
    raise last_exc
