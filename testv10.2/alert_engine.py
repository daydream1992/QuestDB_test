"""testv10.2 统一预警引擎 (alert_engine) — 多源异动聚合 → send_warn + 飞书卡

脚本路径: K:/QuestDB_test/testv10.2/alert_engine.py
从 sentiment._check_alert 泛化: sentiment 只管算分, 预警归此 (职责分离)。
读各计算模块 last_result (sentiment 大盘 / tail 炸板 / 后续 rotation 板块退潮), 聚合判定, 统一出口。
L1大盘异动 → 读 pool(L2领跌板块) + drilled(L3炸板龙头) 定位, 出联动卡。
"""

import bootstrap
bootstrap.ensure_paths()

from collections import deque  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402


class AlertEngine:
    """统一预警: 多源 result → 异动检测 → L2/L3 定位 → on_dive_alert/on_blast_alert。"""

    def __init__(self, ms=None, pub=None, dry_run: bool | None = None, duck=None):
        self.ms = ms
        self.pub = pub
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.duck = duck
        self._hist: deque = deque(maxlen=cfg.DIVE_HISTORY_ROUNDS + 1)   # 大盘指标 history
        self._last_dive_ts: datetime | None = None   # 跳水冷却 (防下滑持续连发)
        self._blast_pushed = False                   # 尾盘炸板汇总只推一次 (降频)

    def check(self, results: dict, pool, drilled: dict, now: datetime, stage: str) -> None:
        """results = {模块名: last_result}; 多源检测 + 定位 + 发。失败不崩。
        _check_dive 用 sentiment history (盘中/tail/close 有新值); _check_blast 只 tail 段。"""
        if not self.pub:
            return
        try:
            self._check_dive(results.get('sentiment'), pool, drilled, now)
            if stage == 'tail':
                self._check_blast(results.get('tail'), now)
        except Exception:  # noqa: BLE001  预警失败不崩 funnel
            logger.exception('alert_engine 异常, 跳过')

    # ── L1 大盘跳水 (sentiment history) + L2/L3 定位 ──
    def _check_dive(self, sent, pool, drilled, now):
        if not sent:
            return
        self._hist.append({'fbl': sent['fbl'], 'blasted': sent['blasted'],
                           'loss_ratio': sent['loss_ratio'], 'score': sent['score'],
                           'zt_cnt': sent['zt_cnt'], 'label': sent.get('label', ''),
                           'max_lb': sent.get('max_lb', 0)})
        dive = self._detect_dive()
        if not dive:
            return
        # 冷却: 15min 内只推一次 (下滑持续时窗口滑动会连发)
        if self._last_dive_ts is not None and \
                (now - self._last_dive_ts).total_seconds() < cfg.DIVE_COOLDOWN_SEC:
            logger.debug('跳水冷却中, 跳过 (上次 {})', self._last_dive_ts.strftime('%H:%M'))
            return
        reasons, past, cur = dive
        boards, stocks = self._locate(pool, drilled)
        # DuckDB 落表 (盘后复盘"几点跳水/什么触发")
        if self.duck:
            try:
                self.duck.record_event('跳水', '-', '-',
                                       ' · '.join(reasons), now)
            except Exception:  # noqa: BLE001
                pass
        if self.pub.on_dive_alert({'reasons': reasons, 'cur': cur, 'past': past,
                                   'boards': boards, 'stocks': stocks}, now):
            self._last_dive_ts = now
            logger.warning('🔴 预警(大盘跳水): {}', ' · '.join(reasons))

    def _detect_dive(self):
        if len(self._hist) < 3:
            return None
        cur, past = self._hist[-1], self._hist[0]
        reasons = []
        if past['fbl'] - cur['fbl'] >= cfg.DIVE_FBL_DROP:
            reasons.append(f"封板率{past['fbl']:.0f}%→{cur['fbl']:.0f}%")
        if (cur['blasted'] >= past['blasted'] * 2
                and cur['blasted'] - past['blasted'] >= cfg.DIVE_BLAST_DOUBLE_MIN):
            reasons.append(f"炸板{past['blasted']}→{cur['blasted']}")
        if (cur['loss_ratio'] >= cfg.DIVE_LOSS_RATIO
                and cur['loss_ratio'] >= past['loss_ratio'] * 1.5):
            reasons.append(f"亏钱比{past['loss_ratio']:.1f}→{cur['loss_ratio']:.1f}")
        if past['score'] >= cfg.DIVE_SCORE_FROM and cur['score'] <= cfg.DIVE_SCORE_TO:
            reasons.append(f"综合分{past['score']:.0f}→{cur['score']:.0f}")
        return (reasons, past, cur) if reasons else None

    def _locate(self, pool, drilled):
        boards, stocks = [], []
        if pool is not None:
            try:
                snap = pool.snapshot()
            except Exception:  # noqa: BLE001
                snap = []
            weak = [s for s in snap if s.state in ('WARN', 'DEAD')] or list(snap)
            weak = sorted(weak, key=lambda s: getattr(s, 'zaf', 0))[:3]
            boards = [(s.name, getattr(s, 'zaf', 0.0), s.state) for s in weak]
        if drilled:
            bad = sorted(((c, d) for c, d in drilled.items() if d.get('ZAF', 0) < -2),
                         key=lambda x: x[1].get('ZAF', 0))[:5]
            for c, d in bad:
                zaf = d.get('ZAF', 0.0)
                role = '近跌停' if zaf <= -9.5 else ('炸板/无封' if d.get('FCAmo', 0) <= 0 else '深跌')
                name = self.ms.stock_name(c) if self.ms else c
                stocks.append((name, zaf, role))
        return boards, stocks

    # ── 个股炸板 (tail result) ──
    def _check_blast(self, tail, now):
        if not tail or tail.get('blast_n', 0) <= 0:
            return
        # 降频: 尾盘炸板汇总只推一次 (15:00 前), 避免与盲区炸板实时重复刷屏
        if self._blast_pushed:
            return
        self._blast_pushed = True
        self.pub.on_blast_alert(tail, now)
