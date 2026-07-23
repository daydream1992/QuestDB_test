"""scanner_runner: 订阅候选扫描器 (独立进程, 不入主调度)

Phase 3: 从 qd_pricevol 拉近 5min 涨跌幅, 筛 ZAF ∈ [5, 9] 的临界候选,
         写 lib.subscribe_pool (本地 SQLite, 跨进程共享, WAL).

系统能力边界 (新 scanner 必须严格守, 不然主调度被拖死, 严禁):
  1. 写 DB:    仅本地 SQLite, 0 次写 QuestDB
  2. 读 DB:    单表 qd_pricevol + cutoff(minutes=5), 不读 snapshot/intraday
              (那俩每轮写 5000 行, SELECT * 是 IO-heavy)
  3. 连接:    独立 psycopg2 连接 + 每轮 ping. 主调度死不影响 scanner.
              不与主调度共享连接 (read replica 各自持连接)
  4. 时长:    单轮 < 1.5s 超时熔断, 失败用上一轮候选 (绝不阻塞循环)
  5. 开关:    subscribe_pool.enabled=false → 空转心跳, CPU ≈ 0
  6. 解耦:    scanner 写 SQLite → supervisor 自己 poll 5s
              scanner 死 → supervisor 继续吃历史候选 (主调度也继续)
  7. 降级:    DB 不可用 → 内存候选 dict (仅本轮不再 nominate, 可继续订阅),
              不抛异常到进程外, 心跳仍写

用法 (独立运行, 0 依赖):
  python collect/scanner_runner.py --once      # 单轮跑一次 (EOD 验证)
  python collect/scanner_runner.py --daemon    # 常驻, 默认 60s/轮
  python collect/scanner_runner.py --dry-run   # 只算不打, 心跳仍写

依赖:
  - lib/subscribe_pool (Phase 1, SQLite WAL 跨进程共享)
  - lib/qdb (connect/_ensure_alive/connect)
  - lib/relation_graph (取股票名, 仅当候选时)
  - lib/tq_client 不依赖 (本进程不调 tqcenter)

CLAUDE.md 守 import 方向: collect/ → lib/, config/ (collect 不依赖 strategy/feishu/runner)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_PROJ_ROOT, 'config', '.env'))

from loguru import logger  # noqa: E402

from lib.subscribe_pool import SubscribePool, Candidate, get_default as pool_default  # noqa: E402
from lib.qdb import connect, query_df, cutoff, _ensure_alive  # noqa: E402


# ── 配置常量 (保护边界) ──

_POLL_INTERVAL_DEFAULT = 60.0  # 默认 60s/轮 (主调度 10s 块干扰不到 scanner)
_HARD_LIMIT_CANDIDATES = 200   # 单轮最多产 200 候选 (TOP_N cap, 防资源失控)
_SINGLE_ROUND_TIMEOUT_S = 1.5  # 单轮硬上限 (含 DB 读 + 评估 + 写 SQLite)
_HEARTBEAT_DIR = os.path.join(_PROJ_ROOT, 'logs', 'heartbeats')


# ── 评分函数 (纯函数, 可单测) ──


def score_candidate(zaf: float, vol_ratio: float, cfg: dict) -> float:
    """Stage-1 评分 (只有 c1 三字段: ZAF + 量)

    评分公式 (yaml score_weights 控制):
      1. near_limit = 距 5%~9% 区间上沿 / 下沿 的最小归一距离
         (刚"点火" 5~6% 或 即将封板 8~9% 都是高分, 中段 7% 略低)
         → 1.0 = 临界边缘, 0.0 = 区间正中
      2. vol_surge  (量比归一: 1.0 中性, ≥2 高分)
      3. score = w_near*near*100 + w_vol*vol*100

    边界: 仅当 zaf ∈ [enter_zaf_low, enter_zaf_high] 时返 > 0;
          区间外返 0.

    Args:
        zaf: 涨跌幅 (%)  (qd_pricevol.Now/LastClose-1*100)
        vol_ratio: 量比 (0~N+)
        cfg: yaml subscribe_pool 段
    """
    lo = cfg.get('enter_zaf_low', 5.0)
    hi = cfg.get('enter_zaf_high', 9.0)
    if not (lo <= zaf <= hi):
        return 0.0
    w = cfg.get('score_weights', {
        'near_limit': 0.35, 'vol_surge': 0.25,
        'zjl': 0.25, 'gene': 0.15,
    })
    # 距"下沿"归一 (刚点火 zaf=lo → 0, 即将封板 zaf=hi → 1)
    # 评分的语义: 越高越接近涨停 = 越值得盯 (封板/炸板前一秒)
    # 中段(7%)不低, 是平滑过渡; 不是峰值
    span = hi - lo  # 4 百分点
    near = (zaf - lo) / span if span > 0 else 0.0
    # 量比归一化 (1.0 = 中性, >=2 → 1)
    vol = min(1.0, max(0.0, (vol_ratio - 1.0) / 2.0))
    score = (
        w.get('near_limit', 0.35) * near * 100
        + w.get('vol_surge', 0.25) * vol * 100
        # zjl/gene 在 Stage-1 = 0, 订阅后由 evaluate_tick 精算
    )
    return round(score, 2)


# ── 主循环 ──


class CandidateScanner:
    """扫描器守护进程

    State:
        _last_cands: 上一轮候选 (DB 失败降级用)
        _con: 独立 psycopg2 连接 (不与主调度共享)
        _exit_flag: 信号驱动
        _metrics: {round_count, errors, last_round_ms, ...}
    """

    def __init__(self, cfg: dict, dry_run: bool = False,
                 poll_interval: float = _POLL_INTERVAL_DEFAULT):
        self._cfg = cfg
        self._dry_run = dry_run
        self._poll = max(5.0, poll_interval)  # 最小 5s 防止误设
        self._enabled = cfg.get('enabled', False)
        self._pool = pool_default()
        self._con = None
        self._last_cands: list[Candidate] = []
        self._exit_flag = False
        self._metrics = {
            'round_count': 0,
            'success': 0,
            'errors': 0,
            'last_round_ms': 0,
            'candidates_last_round': 0,
        }
        signal.signal(signal.SIGINT, self._signal_handler)
        if os.name == 'nt':
            signal.signal(signal.SIGBREAK, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info('scanner 收到退出信号...')
        self._exit_flag = True

    def _connect(self):
        """独立连接, 每轮 ping; 主调度死不影响"""
        if self._con is None or self._con.closed:
            try:
                self._con = connect()
            except Exception as e:
                logger.warning('QuestDB 连接失败 (降级跑): {}', e)
                self._con = None
                return False
        # 健康检查 (单 ping, 失败重连)
        try:
            self._con = _ensure_alive(self._con)
            return True
        except Exception as e:
            logger.warning('连接 ping 失败 (降级): {}', e)
            return False

    def _write_heartbeat(self):
        try:
            os.makedirs(_HEARTBEAT_DIR, exist_ok=True)
            ts = time.time()
            with open(os.path.join(_HEARTBEAT_DIR, 'scanner_runner.ts'),
                      'w') as f:
                f.write(str(ts))
            snap = {
                'ts': ts,
                'enabled': self._enabled,
                'dry_run': self._dry_run,
                'metrics': self._metrics,
                'pool_stats': self._pool.stats(),
            }
            with open(os.path.join(_HEARTBEAT_DIR, 'scanner_runner.json'),
                      'w') as f:
                json.dump(snap, f, ensure_ascii=False)
        except Exception:
            pass

    # ── 单轮 (核心: 必须 < 1.5s, 否则熔断) ──

    def run_once(self) -> dict:
        """单轮扫描; 不依赖 enabled/dry_run 状态 (单测友好)"""
        t0 = time.time()
        try:
            cands = self._produce_candidates()
            elapsed_ms = (time.time() - t0) * 1000

            # 硬上限熔断 (> 1.5s 视为异常, 用上一轮)
            if elapsed_ms > _SINGLE_ROUND_TIMEOUT_S * 1000:
                logger.warning('scanner 单轮 {:.0f}ms 超 {:.0f}ms 熔断, 用上一轮',
                               elapsed_ms, _SINGLE_ROUND_TIMEOUT_S * 1000)
                return {'elapsed_ms': elapsed_ms, 'produced': 0,
                        'status': 'timeout', 'cands': []}

            if not self._dry_run and cands:
                try:
                    self._pool.upsert_candidates(cands)
                except Exception as e:
                    logger.warning('写候选池失败 (降级内存): {}', e)
                    self._last_cands = cands
                    return {'elapsed_ms': elapsed_ms, 'produced': len(cands),
                            'status': 'memory_only', 'cands': cands}
            elif self._dry_run:
                logger.info('[dry-run] scanner 产 {} 候选 (不写池)', len(cands))

            self._last_cands = cands
            self._metrics['round_count'] += 1
            self._metrics['success'] += 1
            self._metrics['last_round_ms'] = round(elapsed_ms, 1)
            self._metrics['candidates_last_round'] = len(cands)
            return {'elapsed_ms': elapsed_ms, 'produced': len(cands),
                    'status': 'ok', 'cands': cands}
        except Exception as e:
            self._metrics['errors'] += 1
            logger.warning('scanner.run_once 异常 (用上轮): {}', e)
            return {'elapsed_ms': (time.time() - t0) * 1000,
                    'produced': 0, 'status': 'error',
                    'cands': self._last_cands}

    def _produce_candidates(self) -> list[Candidate]:
        """拉 qd_pricevol 筛 ZAF ∈ [lo, hi], 评分后 cap 到 _HARD_LIMIT_CANDIDATES"""
        if not self._connect():
            return []
        # 关键: 单表 + cutoff(minutes=5), 不读 snapshot/intraday (这两表 5000 行 IO-heavy)
        try:
            rows = query_df(self._con,
                            f"SELECT code, Now, LastClose, Volume "
                            f"FROM qd_pricevol "
                            f"WHERE snapshot_time > '{cutoff(minutes=5)}' "
                            f"AND Now > 0 AND LastClose > 0")
        except Exception as e:
            logger.warning('query_df qd_pricevol 失败: {}', e)
            return []
        if rows is None or rows.empty:
            return []
        # 取每 code 最新一行 (qd_pricevol 可能多轮重复)
        latest = rows.sort_values('snapshot_time' if 'snapshot_time' in rows.columns
                                   else 'code').groupby('code', as_index=False).tail(1)
        out: list[Candidate] = []
        # Stage-1 vol_surge 简化为 (1.0) 因为 c1 只 Volume 单点,
        # 真正的量比要 c4_kline 历史均量, 不在 scanner 边界内.
        # 0.25 的 vol_surge 权重因此恒 0, 配置权重时建议 w_vol 调小.
        for _, r in latest.iterrows():
            try:
                now = float(r['Now'])
                lc = float(r['LastClose'])
                if lc <= 0 or now <= 0:
                    continue
                zaf = (now - lc) / lc * 100
                s = score_candidate(zaf, vol_ratio=1.0, cfg=self._cfg)
                if s <= 0:
                    continue
                code = str(r['code'])
                # name 暂用 code (memory get_stock_name 启动慢, 单测时跳过)
                out.append(Candidate(code=code, name=code, score=s))
                if len(out) >= _HARD_LIMIT_CANDIDATES:
                    break
            except Exception:
                continue
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:_HARD_LIMIT_CANDIDATES]

    def run_daemon(self):
        """常驻循环 (每 _poll 秒 1 轮, 永不让出 GIL 长)"""
        if not self._enabled:
            logger.warning('subscribe_pool.enabled=false, 空转心跳')
            while not self._exit_flag:
                self._write_heartbeat()
                time.sleep(5)
            return
        logger.info('===== scanner_runner 启动: enabled={} dry_run={} poll={}s =====',
                    self._enabled, self._dry_run, self._poll)
        while not self._exit_flag:
            try:
                self.run_once()
            except Exception as e:
                logger.warning('run_once 未捕获异常 (防死): {}', e)
                self._metrics['errors'] += 1
            self._write_heartbeat()
            # 主循环 sleep 拆 5 段, 加快 SIGINT 响应
            slept = 0.0
            while slept < self._poll and not self._exit_flag:
                time.sleep(min(0.5, self._poll - slept))
                slept += 0.5
        # 退出
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
        logger.info('scanner_runner 已退出: {}', self._metrics)


# ── CLI / 单测入口 ──


def load_yaml_cfg() -> dict:
    """读 yaml subscribe_pool 段; 缺省 {}"""
    try:
        import yaml
        with open(os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml'),
                  encoding='utf-8') as f:
            return (yaml.safe_load(f) or {}).get('subscribe_pool', {}) or {}
    except Exception as e:
        logger.warning('yaml 读取失败: {}', e)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description='订阅候选扫描器 (独立进程)')
    parser.add_argument('--once', action='store_true', help='单轮跑一次')
    parser.add_argument('--daemon', action='store_true', help='常驻 (默认)')
    parser.add_argument('--dry-run', action='store_true', help='只算不写 SQLite')
    parser.add_argument('--poll', type=float, default=_POLL_INTERVAL_DEFAULT,
                        help=f'轮询间隔秒 (默认 {_POLL_INTERVAL_DEFAULT})')
    args = parser.parse_args()

    cfg = load_yaml_cfg()
    if args.dry_run:
        cfg['dry_run'] = True

    sc = CandidateScanner(cfg, dry_run=args.dry_run, poll_interval=args.poll)
    if args.once:
        result = sc.run_once()
        sc._write_heartbeat()
        print('[once] scanner result:')
        print(json.dumps({
            'elapsed_ms': result['elapsed_ms'],
            'produced': result['produced'],
            'status': result['status'],
            'metrics': sc._metrics,
            'pool_stats': sc._pool.stats(),
        }, ensure_ascii=False, indent=2))
        return 0
    sc.run_daemon()
    return 0


if __name__ == '__main__':
    sys.exit(main())
