"""子进程注册表 (集中管理)

脚本路径: K:\QuestDB_test\runner\process_registry.py
用途: 集中管理所有子进程的注册/注销/状态查询
依赖: 无第三方依赖 (不 import tqcenter / psutil / qdb)
设计:
  - 模块级 dict 存储 {tag: ProcessInfo}
  - overseer / scheduler 共用此注册表, 避免重复 psutil 遍历
  - 纯内存 dict 操作, 零 IO, 性能 O(1)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ProcessInfo:
    """单个子进程注册信息"""
    reg_name: str           # 注册名 (中文)
    tag: str                # 英文 tag
    script_path: str        # 脚本路径 (相对于项目根)
    proc_type: str          # 'subprocess' | 'oneshot'
    pid: Optional[int] = None
    status: str = 'idle'    # 'idle' | 'running' | 'stopped'
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None


# ── 注册表本体 ──
_PROCESS_REGISTRY: dict[str, ProcessInfo] = {}

# ── 注册表定义 ──
_REGISTRATION_TABLE = [
    ('竞价监控',    'auction_monitor', 'runner/auction_monitor.py', 'subprocess'),
    ('盘中主循环',   'intraday_loop',   'runner/intraday_loop.py',   'subprocess'),
    ('订阅推送',    'subscribe',       'compute/subscribe.py',      'subprocess'),
    ('监工守护',    'overseer',        'runner/overseer.py',        'subprocess'),
    ('盘前初始化',   'daily_init',      'runner/daily_init.py',      'oneshot'),
    ('盘后更新',    'daily_close',     'runner/daily_close.py',     'oneshot'),
    ('复盘汇总',    'daily_summary',   'runner/daily_summary.py',   'oneshot'),
    ('表结构校验',   'verify_tables',   'scripts/verify_tables.py',  'oneshot'),
]


def initialize():
    """初始化注册表 (幂等, 多次调用安全)"""
    if _PROCESS_REGISTRY:
        return
    for name, tag, script, ptype in _REGISTRATION_TABLE:
        _PROCESS_REGISTRY[tag] = ProcessInfo(
            reg_name=name, tag=tag,
            script_path=script, proc_type=ptype,
        )


def register_process(tag: str, pid: int):
    """注册子进程 (scheduler 启动时调用)"""
    info = _PROCESS_REGISTRY.get(tag)
    if info:
        info.pid = pid
        info.status = 'running'
        info.started_at = datetime.now()


def unregister_process(tag: str):
    """注销子进程 (子进程退出时调用)"""
    info = _PROCESS_REGISTRY.get(tag)
    if info:
        info.pid = None
        info.status = 'stopped'
        info.stopped_at = datetime.now()


def get_process(tag: str) -> Optional[ProcessInfo]:
    """查询单个进程状态"""
    return _PROCESS_REGISTRY.get(tag)


def get_all_processes() -> dict[str, ProcessInfo]:
    """获取全部进程状态"""
    return dict(_PROCESS_REGISTRY)


def is_running(tag: str) -> bool:
    """快捷查询: 进程是否在运行"""
    info = _PROCESS_REGISTRY.get(tag)
    return info is not None and info.status == 'running' and info.pid is not None
