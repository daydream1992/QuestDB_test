"""sub_pool_supervisor: 动态订阅池调度器 (独立进程, 不动现存 subscribe.py/intraday_loop.py)

本文件是 Phase 2.A. C 方案落点: 完全旁路. 现存 compute/subscribe.py 仍然可独立
启动 (python compute/subscribe.py), runner/intraday_loop.py 完全不知此进程存在.

职责划分:
  - 现役 subscribe.py
      on_data → 字段落库 (qd_stock_snapshot + qd_stock_intraday) → DB
      (订阅清单硬编码 _DEFAULT_WATCH)
  - 本进程 sub_pool_supervisor
      主循环: produce 候选 → maintain 订阅池 (增量订阅/触发即弃) → cooldown
      触发即弃的 tick 进入 evaluate_tick (复用 strategy/intraday_engine.detect_*)
      推送频控: 复用 lib.notify_dedup.allow_push (180s TTL, critical 旁路但受 L1 10s 闸)

启动方式:
  python runner/sub_pool_supervisor.py             # 接管 (yaml enabled=true)
  python runner/sub_pool_supervisor.py --dry-run   # 只产候选, 不真订阅
  python runner/sub_pool_supervisor.py --once      # 单轮跑完即退出 (EOD 验证)
  Ctrl+C 退出

藕合设计: 可随时关停, 把 yaml 的 subscribe_pool.enabled 改回 false 即可,
整个进程退掉后老 subscribe.py 独立跑零感知. 主调度 intraday_loop 不动一行.

依赖:
  - lib/subscribe_pool (Phase 1)
  - lib/tq_client (线程安全 + 3 次重试)
  - lib/notify_dedup (推送频控 180s)
  - lib/relation_graph (取股票名)
  - lib/qdb (心跳/历史落库, DEDUP UPSERT)
  - strategy/intraday_engine (复用 detect_limit/detect_surge/detect_capital)

CLAUDE.md 守 import 方向: runner/ → compute/ strategy/ lib/ ddl/ config/
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime

# ── 路径与配置加载 ──

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_PROJ_ROOT, 'config', '.env'))

from loguru import logger  # noqa: E402

from lib.subscribe_pool import SubscribePool, Candidate, get_default as pool_default  # noqa: E402
from lib.tq_client import safe_call, init as tq_init, close as tq_close  # noqa: E402
from lib.notify_dedup import allow_push  # noqa: E402
from lib.relation_graph import get_stock_name  # noqa: E402
from lib.qdb import connect, executemany_batch, cutoff  # noqa: E402

# tq 直接引用 (subscribe_hq / unsubscribe_hq), 走 safe_call 锁
from tqcenter import tq  # noqa: E402

# 复用既有异动检测 (不复制 detect_limit/surge/capital 逻辑)
from strategy.intraday_engine import (  # noqa: E402
    MonitorState,
    detect_surge,
    detect_limit,
    detect_capital,
)


# ── 常量 ──

_POLL_INTERVAL = 1.0           # 主循环 sleep
_HEARTBEAT_DIR = os.path.join(_PROJ_ROOT, 'logs', 'heartbeats')
_POOL_HISTORY_TBL = 'qd_subscription_pool_history'
_POOL_HISTORY_COLS = [
    'event_time', 'code', 'action', 'reason',
    'score', 'pool_size', 'rationale',
]


# ── 评价触发器 (本进程核心决策点) ──


@dataclass
class Trigger:
    """单 tick 一次触发 (携带 unload 决策 + critical 标记)

    字段契约与 intraday_engine.detect_all 返回的 4 元组兼容
    (code, event_type, desc, critical) —— 这里是 5 元组的 dataclass 形态,
    多一个 unload 布尔, 因为订阅模块需要"是否腾退"的决策.
    """
    code: str
    etype: str              # 'limit_break' / 'break_down' / 'vol_price_div'
                            # / 'surge_up' / 'capital_in' / 'limit_seal' 等
    desc: str               # 人类可读描述
    critical: bool = False  # 是否旁路 180s Deduper
    unload: bool = False    # True = 触发即弃 (unsubscribe_hq)
    rationale: str = ''     # 触发细则, 写入 qd_subscription_pool_history


# MonState 跨 tick: peak_zaf (峰值) / zaf_5min_ago (回看), 满足
# "峰值回撤 ≥ break_drop_pct" 判定破位. 模块级 dict, 每 code 一份.
@dataclass
class MonState:
    last_limit_sealed: bool = False  # 与 intraday_engine.MonitorState 字段兼容
    peak_zaf: float = 0.0
    zaf_5min_ago: float = 0.0
    zaf_5min_ts: float = 0.0

    def touch_5min_ago(self, now_ts: float):
        """每 5 分钟记录一次 zaf, 给 vol_price_div 判定"""
        if now_ts - self.zaf_5min_ts > 300:
            self.zaf_5min_ago = self.peak_zaf
            self.zaf_5min_ts = now_ts


_STATES: dict[str, MonState] = {}


def _state(code: str) -> MonState:
    if code not in _STATES:
        _STATES[code] = MonState()
    return _STATES[code]


def _compat_monitor_state(ms: MonState) -> MonitorState:
    """包一层给 intraday_engine.detect_limit 用 (复用原有逻辑, 不复制)"""
    st = MonitorState(last_limit_sealed=ms.last_limit_sealed)
    return st


def evaluate_tick(
    code: str,
    f: dict,
    cfg: dict,
) -> list[Trigger]:
    """逐 tick 评价触发 + 是否触发即弃

    Args:
        code: 标的代码
        f: tick 字段 dict (已合并 get_market_snapshot + get_more_info.intraday)
        cfg: yaml subscribe_pool 段

    Returns:
        list[Trigger]: 可能多个 (例如 同时 break_down + vol_price_div)
    """
    now = _to_float(f.get('ZAF', 0))
    fcamo = _to_float(f.get('FCAmo', 0))
    zjl = _to_float(f.get('Zjl', 0))
    f_lian_b = _to_float(f.get('fLianB', 0))

    ms = _state(code)
    ms.peak_zaf = max(ms.peak_zaf, now)

    # 复用既有 detect_limit (FCAmo 权威, 守 CLAUDE.md §四)
    inner_state = _compat_monitor_state(ms)
    limit_res = detect_limit(inner_state, fcamo)
    # 写回 (detect_limit 内部已改 last_limit_sealed)
    ms.last_limit_sealed = inner_state.last_limit_sealed

    triggers: list[Trigger] = []
    if limit_res:
        etype, desc, critical = limit_res
        if etype == 'limit_break':
            # 炸板: 即弃 + critical
            triggers.append(Trigger(
                code=code, etype=etype, desc=desc,
                critical=True, unload=True,
                rationale=f'FCAmo {fcamo}→0 from sealed',
            ))
        elif etype == 'limit_seal':
            # 封死: 推 1 次 watch 但**保持订阅** (继续盯破板)
            triggers.append(Trigger(
                code=code, etype=etype, desc=desc,
                critical=False, unload=False,
                rationale=f'FCAmo>0, sealed {fcamo}',
            ))

    # 破位下跌: 峰值回撤 ≥ 阈 (默认 3%) 或跌破 enter_zaf_low
    drop = ms.peak_zaf - now
    zaf_low = cfg.get('enter_zaf_low', 5.0)
    drop_pct = cfg.get('break_drop_pct', 3.0)
    if drop >= drop_pct or (ms.peak_zaf >= zaf_low + 1 and now < zaf_low):
        triggers.append(Trigger(
            code=code, etype='break_down',
            desc=f'破位回落 {drop:.1f}% (peak={ms.peak_zaf:.1f}%, now={now:.1f}%)',
            critical=False, unload=True,
            rationale=f'peak_drop={drop:.2f}% threshold={drop_pct}%',
        ))

    # 量价背离: 量比 ≥ 阈 且 5min 涨幅停滞
    div_ratio = cfg.get('div_vol_ratio', 2.0)
    div_stall = cfg.get('div_zaf_stall', 0.5)
    zaf_5min_chg = now - ms.zaf_5min_ago
    if f_lian_b >= div_ratio and zaf_5min_chg < div_stall:
        triggers.append(Trigger(
            code=code, etype='vol_price_div',
            desc=f'量增价滞 量比 {f_lian_b:.1f} 5min涨幅 {zaf_5min_chg:.2f}%',
            critical=False, unload=True,
            rationale=f'lian_b={f_lian_b:.2f} stall_chg={zaf_5min_chg:.2f}%',
        ))

    # 复用 detect_surge (非 unload, 仅记录 — 频控接管)
    surge_res = detect_surge(_to_float(f.get('Now', 0)),
                            _to_float(f.get('Before5MinNow', 0)))
    if surge_res and zaf_low <= now <= cfg.get('enter_zaf_high', 9.0):
        etype, desc, _ = surge_res
        triggers.append(Trigger(
            code=code, etype=etype, desc=desc,
            critical=False, unload=False, rationale='surge_in_band',
        ))

    # 复用 detect_capital
    cap_res = detect_capital(zjl * 1e4)  # detect_capital 单位是 "元", 这里 Zjl 是万元
    if cap_res:
        etype, desc, _ = cap_res
        triggers.append(Trigger(
            code=code, etype=etype, desc=desc,
            critical=False, unload=False, rationale=f'Zjl={zjl}万',
        ))

    return triggers


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── 推送 (levelup allow_push) ──


def push_trigger(t: Trigger, cfg: dict):
    """推送决策: 走 lib.notify_dedup.allow_push (180s TTL)

    Critical (limit_break) 旁路 180s, 但仍受 L1 10s 抖动闸 (code+etype)
    """
    # 用 allow_push 复用现有频控 (跨进程安全的 dedup 状态)
    # critical=True 旁路 180s; L1 抖动闸由调用方在 deal_with_triggers 内串
    if allow_push(t.code, t.etype, critical=t.critical):
        # 真正飞书推送留给 feishu (不在本进程做, 走现有 feishu.* 接口)
        # 这里只记 signal 列表: 让 feishu 推送层 (与现有通知路径一致)
        logger.info('[push] {} {} {} (crit={})',
                    t.code, t.etype, t.desc, t.critical)
        return True
    return False


# ── 主循环 ──


class SubPoolSupervisor:
    """订阅池调度器 — 单进程生命周期管理

    状态机:
        produce:  写候选 (Phase 3 由 intraday_loop 接, 此进程也能跑)
        maintain: 每 1s 维护订阅池 (补名额 / 卸载冷却)
        on_data:  tqcenter 推送回调 (重写 — 不复用 compute.subscribe.on_data 任何逻辑,
                 字段落库由独立路径负责)

    藕合:
        enabled=false 时 start() 直接 return; 进程退化为纯 CLI 自检
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._cap = cfg.get('cap', 95)
        self._enabled = cfg.get('enabled', False)
        self._dry_run = cfg.get('dry_run', False)
        self._cooldown_sec = cfg.get('cooldown_sec', 300)
        self._debounce_sec = cfg.get('debounce_sec', 10)
        self._cooldown = pool_default()  # 复用 lib.subscribe_pool 默认实例
        self._subscribed: dict[str, str] = {}  # 当前订阅 code → name
        self._lock = threading.Lock()
        self._exit_flag = False
        self._last_trigger_ts: dict[tuple[str, str], float] = {}  # L1 抖动闸 (code, etype)
        self._con = None  # qdb 连接 (历史/快照落库)
        signal.signal(signal.SIGINT, self._signal_handler)
        if os.name == 'nt':
            signal.signal(signal.SIGBREAK, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info('收到退出信号, 关闭订阅池...')
        self._exit_flag = True

    # ── 心跳 ──

    def _write_heartbeat(self):
        try:
            os.makedirs(_HEARTBEAT_DIR, exist_ok=True)
            ts = time.time()
            with open(os.path.join(_HEARTBEAT_DIR, 'sub_pool_supervisor.ts'),
                      'w') as f:
                f.write(str(ts))
            # 同步写一份状态 JSON (主调度 / 调试可读)
            snap = {
                'ts': ts,
                'enabled': self._enabled,
                'dry_run': self._dry_run,
                'subscribed_count': len(self._subscribed),
                'subscribed_codes': sorted(self._subscribed.keys()),
                'pool_stats': self._cooldown.stats(),
            }
            with open(os.path.join(_HEARTBEAT_DIR, 'sub_pool_supervisor.json'),
                      'w') as f:
                json.dump(snap, f, ensure_ascii=False)
        except Exception:
            pass

    # ── DB 工具 ──

    def _db(self):
        with self._lock:
            if self._con is None or self._con.closed:
                self._con = connect()
            return self._con

    def _log_history(self, code: str, action: str, reason: str,
                     score: float = 0.0, rationale: str = ''):
        """落 qd_subscription_pool_history (DEDUP UPSERT 幂等)"""
        if self._dry_run:
            return
        try:
            rows = [(datetime.now(), code, action, reason, score,
                     len(self._subscribed), rationale)]
            executemany_batch(self._db(), _POOL_HISTORY_TBL,
                              _POOL_HISTORY_COLS, rows)
        except Exception as e:
            logger.warning('qd_subscription_pool_history 写失败: {}', e)

    # ── 维护 ──

    def _maintain(self):
        """每 1s 调一次: 补名额 + 维护心跳"""
        if not self._exit_flag:
            self._write_heartbeat()
        # 补名额: cap 没满, 从池中拉
        need = self._cap - len(self._subscribed)
        if need <= 0:
            return
        try:
            cands = self._cooldown.top_candidates(
                limit=need, exclude=set(self._subscribed.keys()),
            )
        except Exception as e:
            logger.warning('top_candidates 失败: {}', e)
            return
        for c in cands:
            self._subscribe_one(c)

    def _subscribe_one(self, c: Candidate):
        """真订阅 1 只 (增量); dry_run 时只记心跳"""
        if self._dry_run:
            self._subscribed[c.code] = c.name
            logger.info('[dry-run] subscribe {} ({}) score={}',
                        c.code, c.name, c.score)
            self._log_history(c.code, 'add', 'rotation', c.score, 'dry_run')
            return
        try:
            res = safe_call(tq.subscribe_hq, stock_list=[c.code],
                            callback=self._on_data)
            if res is not False and res != 0:  # tq 返回 truthy 即成功
                self._subscribed[c.code] = c.name
                logger.info('subscribe {} ({}) score={}', c.code, c.name, c.score)
                self._log_history(c.code, 'add', 'rotation', c.score)
            else:
                logger.warning('subscribe_hq 拒收 {} 返回={}', c.code, res)
        except Exception as e:
            logger.warning('subscribe_hq 异常 {}: {}', c.code, e)

    def _unsubscribe_one(self, code: str, reason: str, rationale: str = ''):
        """卸载 1 只 (核心: unload + cooldown, 触发即弃路径)"""
        if self._dry_run:
            self._subscribed.pop(code, None)
            logger.info('[dry-run] unsubscribe {} reason={}', code, reason)
            self._log_history(code, 'remove', reason, 0.0, rationale)
            self._cooldown.mark_cooldown(code, reason, self._cooldown_sec)
            return
        try:
            res = safe_call(tq.unsubscribe_hq, stock_list=[code])
            self._subscribed.pop(code, None)
            # 写冷却 (跨进程可读, 漏斗再提名时跳过)
            self._cooldown.mark_cooldown(code, reason, self._cooldown_sec)
            # 历史: DEDUP UPSERT 幂等
            self._log_history(code, 'remove', reason, 0.0, rationale)
            logger.info('unsubscribe {} reason={}', code, reason)
        except Exception as e:
            logger.warning('unsubscribe_hq 异常 {}: {}', code, e)

    def _debounce_ok(self, code: str, etype: str) -> bool:
        """L1 抖动闸 (10s, code+etype, 含 critical)"""
        now = time.time()
        key = (code, etype)
        last = self._last_trigger_ts.get(key, 0.0)
        if now - last < self._debounce_sec:
            return False
        self._last_trigger_ts[key] = now
        return True

    def _deal_with_triggers(self, code: str, fields: dict):
        """on_data 入口: 评价 tick → 触发即弃/推/记历史

        关键契约:
          - 即使不存在 unload, 也会产生 trigger (走记录/推送)
          - critical 信号 (limit_break) 旁路 180s 但仍受 L1 10s 闸
        """
        try:
            triggers = evaluate_tick(code, fields, self._cfg)
        except Exception as e:
            logger.warning('evaluate_tick 异常 {}: {}', code, e)
            return
        for t in triggers:
            if not self._debounce_ok(code, t.etype):
                continue  # L1 抖动闸
            if t.critical or t.etype in ('limit_seal', 'surge_up', 'capital_in'):
                push_trigger(t, self._cfg)
            if t.unload:
                # 触发即弃: 卸载 + 冷却 + 历史
                self._unsubscribe_one(code, reason=t.etype,
                                      rationale=t.rationale)

    def _on_data(self, data_str: str):
        """tqcenter 推送回调 (本进程独有, 不复用 subscribe.py.on_data)

        字段落库职责刻意省略 — 本进程只负责订阅池维护 + 触发即弃;
        完整字段落库仍由 compute/subscribe.py 的 on_data 路径负责 (未来可扩展).
        """
        from datetime import time as dtime
        try:
            now_dt = datetime.now()
            if not (dtime(9, 15) <= now_dt.time() < dtime(11, 30) or
                    dtime(13, 0) <= now_dt.time() < dtime(15, 0)):
                return
            parsed = json.loads(data_str)
            if parsed.get('ErrorId') != '0':
                return
            code = parsed.get('Code')
            if code not in self._subscribed:
                return
            # 取全字段 (与现有 subscribe.py 路径一致, 后续可拆高/低频)
            tick = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[])
            if not tick:
                return
            more = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}
            merged = {**tick, **more}
            self._deal_with_triggers(code, merged)
        except Exception as e:
            logger.warning('_on_data 异常: {}', e)

    # ── 入口 ──

    def start(self):
        """阻塞运行"""
        if not self._enabled:
            logger.warning('subscribe_pool.enabled=false, 跳过 (yaml 改 true 才接管)')
            return
        if self._dry_run:
            logger.warning('=== DRY-RUN 模式: 只产候选/记录, 不真订阅 ===')

        # tqcenter 初始化 (dry_run 跳过)
        if not self._dry_run:
            tq_init()

        # 收盘清理: 启动时对从未处理的 ticker 全部 unsubscribe (防跨日名额泄漏)
        if not self._dry_run:
            try:
                safe_call(tq.unsubscribe_hq, stock_list=[])
            except Exception:
                pass

        logger.info('===== sub_pool_supervisor 启动: enabled={} dry_run={} cap={} =====',
                    self._enabled, self._dry_run, self._cap)
        logger.info('pool_stats: {}', self._cooldown.stats())

        # 主循环
        last_maintain = 0.0
        while not self._exit_flag:
            now = time.time()
            try:
                if now - last_maintain >= self._POLL_INTERVAL:
                    self._maintain()
                    # 每 60s 清过期 (低开销)
                    if int(now) % 60 == 0:
                        try:
                            self._cooldown.expire_cooldowns()
                        except Exception:
                            pass
                    last_maintain = now
            except Exception as e:
                logger.warning('maintain 异常: {}', e)
            time.sleep(0.1)

        # 退出: 全部 unsubscribed + 清冷却
        try:
            if not self._dry_run and self._subscribed:
                codes = list(self._subscribed.keys())
                safe_call(tq.unsubscribe_hq, stock_list=codes)
        except Exception:
            pass
        self._subscribed.clear()
        try:
            tq_close()
        except Exception:
            pass
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
        logger.info('sub_pool_supervisor 已退出')

    def run_once(self) -> dict:
        """单轮跑: 维护 1 次 → 返回心跳快照. 用于 EOD 验证 / 单测.

        Returns:
            dict: {subscribed_count, subscribed_codes, pool_stats}
        """
        self._maintain()
        return {
            'subscribed_count': len(self._subscribed),
            'subscribed_codes': sorted(self._subscribed.keys()),
            'pool_stats': self._cooldown.stats(),
        }


# ── 配置加载 ──


def load_yaml_cfg() -> dict:
    """读 config/strategies.yaml subscribe_pool 段; 不存在时返 {} (默认禁用)"""
    try:
        import yaml
        with open(os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml'),
                  encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get('subscribe_pool', {}) or {}
    except Exception as e:
        logger.warning('yaml 读取失败 (退化为空配置): {}', e)
        return {}


# ── CLI ──


def main():
    parser = argparse.ArgumentParser(description='订阅池调度器 (独立进程)')
    parser.add_argument('--dry-run', action='store_true',
                        help='只产候选/记录, 不真订阅')
    parser.add_argument('--once', action='store_true',
                        help='单轮跑完即退出 (EOD 验证 / 单测)')
    parser.add_argument('--seed-candidate', type=str, default='',
                        help='canary 注入单条候选 (code,name,score)')
    args = parser.parse_args()

    cfg = load_yaml_cfg()
    if args.dry_run:
        cfg['dry_run'] = True

    sup = SubPoolSupervisor(cfg)

    if args.once:
        # 单轮模式: 注 1 个 canary → maintain → 报快照
        if args.seed_candidate:
            parts = [p.strip() for p in args.seed_candidate.split(',')]
            if len(parts) >= 3:
                code, name, score_s = parts[0], parts[1], parts[2]
                try:
                    score = float(score_s)
                except ValueError:
                    score = 80.0
                sup._cooldown.upsert_candidate(Candidate(
                    code=code, name=name, score=score))
        snap = sup.run_once()
        print('[once] sub_pool snapshot:')
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0

    sup.start()
    return 0


if __name__ == '__main__':
    sys.exit(main())
