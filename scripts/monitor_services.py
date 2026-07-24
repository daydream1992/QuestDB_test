"""monitor_services: 全栈服务监工 (QuestDB + 3 个 Python 守护进程)

用途: 一眼看清 4 个关键服务是否健康, 输出明确状态报告.
      不自动重启 (避免误判杀进程), 仅报告 + 退出码供 CI/调度判断.

检查项 (纯文件 + SQL, 不依赖 psutil/tasklist):
  1. QuestDB          → SELECT 1 (端口 8812 通即健康)
  2. intraday_loop    → 心跳 logs/heartbeats/intraday_loop.ts 新鲜度
  3. sub_pool_supervisor → 心跳 logs/heartbeats/sub_pool_supervisor.ts + .json
  4. scanner_runner   → 心跳 logs/heartbeats/scanner_runner.ts + .json
  5. subscribe (旧)   → 心跳 logs/heartbeats/subscribe.ts (可选, --with-legacy)

心跳新鲜度阈值 (区分交易时段, 防午休/盘后误报):
  - 交易时段 (9:15-11:30, 13:00-15:00): 60s 内 = 健康
  - 非交易时段: 120s 内 = 健康 (进程慢速心跳)
  - 心跳文件不存在: UNKNOWN (进程从未启动, 不算挂)

用法:
  python scripts/monitor_services.py                  # 单次报告 (人类可读)
  python scripts/monitor_services.py --watch          # 持续监控, 每 30s 刷新
  python scripts/monitor_services.py --watch --interval 60
  python scripts/monitor_services.py --json           # JSON 输出 (CI)
  python scripts/monitor_services.py --with-legacy    # 含旧 subscribe 进程

退出码:
  0 = 所有启用服务健康
  1 = 至少一个服务不健康 (心跳过期 / QuestDB 不可达)
  2 = 配置错误 (无心跳目录等)

依赖: lib/qdb (connect/query_one) — 复用, 不引入新依赖
CLAUDE.md 守 import 方向: scripts/ → lib/ (scripts 不依赖业务模块)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

# Windows 控制台默认 GBK, 输出 ✓✗ 等 Unicode 会 UnicodeEncodeError.
# 强制 stdout/stderr 用 UTF-8 (Python 3.7+ reconfigure); 失败则降级 ASCII 图标.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
    _UTF8_OK = True
except Exception:
    _UTF8_OK = False

from loguru import logger  # noqa: E402

_HEARTBEAT_DIR = Path(_PROJ_ROOT) / 'logs' / 'heartbeats'


# ── 数据结构 ──


@dataclass
class ServiceStatus:
    """单服务状态 (immutable)"""
    name: str
    healthy: bool               # True=健康, False=异常, None=未知(未启动)
    detail: str                 # 人类可读细节
    age_sec: float = -1.0       # 心跳距今秒数 (-1=无心跳/不适用)


@dataclass
class MonitorResult:
    """整次扫描结果"""
    ts: float
    is_trading: bool
    services: list[ServiceStatus] = field(default_factory=list)

    @property
    def all_healthy(self) -> bool:
        # None (未启动) 不算不健康; False 才算
        return all(s.healthy is not False for s in self.services)

    @property
    def healthy_count(self) -> int:
        return sum(1 for s in self.services if s.healthy is True)

    @property
    def unknown_count(self) -> int:
        return sum(1 for s in self.services if s.healthy is None)


# ── 工具 ──


def is_trading_hours(now: datetime | None = None) -> bool:
    """交易时段判定 (与 subscribe.py / sub_pool_supervisor.py 一致)"""
    now = now or datetime.now()
    t = now.time()
    return (dtime(9, 15) <= t < dtime(11, 30)
            or dtime(13, 0) <= t < dtime(15, 0))


def _heartbeat_age(ts_path: Path) -> float:
    """心跳文件距今秒数; 不存在返 -1"""
    if not ts_path.exists():
        return -1.0
    try:
        mtime = ts_path.stat().st_mtime
        return max(0.0, time.time() - mtime)
    except OSError:
        return -1.0


def _check_heartbeat(name: str, ts_file: str, json_file: str | None,
                     is_trading: bool) -> ServiceStatus:
    """检查单进程心跳新鲜度

    阈值:
      交易时段 60s / 非交易 120s
      无心跳文件 → UNKNOWN (进程从未启动, 不算挂)
    """
    ts_path = _HEARTBEAT_DIR / ts_file
    age = _heartbeat_age(ts_path)
    threshold = 60 if is_trading else 120

    if age < 0:
        return ServiceStatus(name=name, healthy=None,
                             detail='心跳文件不存在 (进程未启动?)', age_sec=-1.0)

    # 读 .json 状态 (可选, 增强细节)
    extra = ''
    if json_file:
        jp = _HEARTBEAT_DIR / json_file
        if jp.exists():
            try:
                j = json.loads(jp.read_text(encoding='utf-8'))
                enabled = j.get('enabled')
                dry = j.get('dry_run')
                if enabled is False:
                    extra = ' [enabled=false 空转]'
                elif dry:
                    extra = ' [dry-run]'
            except Exception:
                pass

    if age <= threshold:
        return ServiceStatus(name=name, healthy=True,
                             detail=f'心跳新鲜 {age:.0f}s 前{extra}', age_sec=age)
    return ServiceStatus(name=name, healthy=False,
                         detail=f'心跳过期 {age:.0f}s (阈 {threshold}s){extra}',
                         age_sec=age)


def _check_questdb() -> ServiceStatus:
    """QuestDB 健康 = SELECT 1 通 (端口 8812)"""
    try:
        from lib.qdb import connect, query_one
        con = connect()
        try:
            row = query_one(con, 'SELECT 1 AS ok')
            if row and row.get('ok') == 1:
                return ServiceStatus(name='QuestDB', healthy=True,
                                     detail='SELECT 1 OK (端口 8812)')
            return ServiceStatus(name='QuestDB', healthy=False,
                                 detail=f'SELECT 1 返回异常: {row}')
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception as e:
        msg = str(e)[:80]
        return ServiceStatus(name='QuestDB', healthy=False,
                             detail=f'连接失败: {msg}')


# ── 主扫描 ──


def scan(with_legacy: bool = False) -> MonitorResult:
    """单次扫描所有服务"""
    trading = is_trading_hours()
    result = MonitorResult(ts=time.time(), is_trading=trading)

    # QuestDB (必查)
    result.services.append(_check_questdb())

    # 主调度
    result.services.append(
        _check_heartbeat('intraday_loop', 'intraday_loop.ts', None, trading))

    # 订阅池守护
    result.services.append(
        _check_heartbeat('sub_pool_supervisor',
                         'sub_pool_supervisor.ts',
                         'sub_pool_supervisor.json', trading))

    # 扫描器
    result.services.append(
        _check_heartbeat('scanner_runner',
                         'scanner_runner.ts',
                         'scanner_runner.json', trading))

    # 旧订阅 (可选)
    if with_legacy:
        result.services.append(
            _check_heartbeat('subscribe (旧)', 'subscribe.ts', None, trading))

    return result


# ── 输出 ──


def _icon(healthy: bool | None) -> str:
    """状态图标 (UTF-8 不可用时降级 ASCII)"""
    if not _UTF8_OK:
        return {True: '[OK]  ', False: '[FAIL]', None: '[?]   '}[healthy]
    if healthy is True:
        return '✓'
    if healthy is False:
        return '✗'
    return '?'


def render_text(result: MonitorResult) -> str:
    """人类可读报告"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    phase = '交易时段' if result.is_trading else '非交易时段'
    lines = [
        f'[{now}] 服务监控报告 ({phase})',
        '━' * 50,
    ]
    for s in result.services:
        lines.append(f'{_icon(s.healthy)} {s.name:<22} {s.detail}')
    lines.append('━' * 50)
    total = len(result.services)
    lines.append(f'汇总: {result.healthy_count}/{total} 健康, '
                 f'{result.unknown_count} 未启动, '
                 f'{total - result.healthy_count - result.unknown_count} 异常')
    return '\n'.join(lines)


def render_json(result: MonitorResult) -> str:
    """JSON 输出 (CI 友好)"""
    return json.dumps({
        'ts': result.ts,
        'is_trading': result.is_trading,
        'all_healthy': result.all_healthy,
        'healthy_count': result.healthy_count,
        'services': [
            {'name': s.name, 'healthy': s.healthy,
             'detail': s.detail, 'age_sec': s.age_sec}
            for s in result.services
        ],
    }, ensure_ascii=False, indent=2)


# ── CLI ──


def main() -> int:
    parser = argparse.ArgumentParser(description='全栈服务监工')
    parser.add_argument('--watch', action='store_true',
                        help='持续监控 (默认单次)')
    parser.add_argument('--interval', type=float, default=30,
                        help='watch 间隔秒 (默认 30)')
    parser.add_argument('--json', action='store_true',
                        help='JSON 输出 (CI)')
    parser.add_argument('--with-legacy', action='store_true',
                        help='含旧 subscribe 进程')
    args = parser.parse_args()

    if not _HEARTBEAT_DIR.exists():
        # 心跳目录不存在 = 主调度从未跑过, 不算配置错误, 但提示
        if not args.json:
            print(f'[warn] 心跳目录不存在: {_HEARTBEAT_DIR}')
            print('[warn] 服务可能从未启动; 先跑 intraday_loop 产生心跳')

    if args.watch:
        logger.info('持续监控模式 (Ctrl+C 退出, 间隔 {}s)', args.interval)
        try:
            while True:
                result = scan(with_legacy=args.with_legacy)
                if args.json:
                    print(render_json(result))
                else:
                    # watch 模式清屏刷新 (Windows cls / Unix clear)
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(render_text(result))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print('\n[exit] 监控已停止')
        return 0

    # 单次模式
    result = scan(with_legacy=args.with_legacy)
    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return 0 if result.all_healthy else 1


if __name__ == '__main__':
    sys.exit(main())
