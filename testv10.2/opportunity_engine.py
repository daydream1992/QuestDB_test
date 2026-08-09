"""testv10.2 机会事件引擎 (opportunity) — 3 正事件: 新主线/趋势确认/龙头封板

脚本路径: K:/QuestDB_test/testv10.2/opportunity_engine.py
职责: 消费 board_pool(状态机) + drilled(钻取) + blindspot(首次封板时间),
      检测 3 类机会事件 → publisher 3 正卡。纯内存, 零新采集。
检测 (每轮, 盘中/tail):
  board_new:  状态机 new_entries (每轮≤2, 去重不重推)
  hot_streak: rounds_in≥3 且未推过 (趋势确认, 非单轮脉冲)
  limit_up:   drilled 涨停股 (FCAmo>0 且 ZAF≥9), 每轮≤2, 每只只推一次
红线: dry_run 默认; ≤2/min bucket 共用 (publisher); 失败不崩 funnel。
"""

import bootstrap
bootstrap.ensure_paths()

from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402


class OpportunityEngine:
    """机会事件引擎: 读各源 → 去重 → publisher 3 正卡。"""

    def __init__(self, ms=None, pub=None, dry_run: bool | None = None):
        self.ms = ms
        self.pub = pub
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self._pushed_new: set[str] = set()     # 已推过的新主线板块 (去重)
        self._pushed_streak: set[str] = set()  # 已推过的趋势确认板块
        self._pushed_limit: set[str] = set()   # 已推过的封板个股

    # ── 每轮检测 (盘中/tail 由 radar 调) ──
    def check(self, new_entries: list[str], pool, drilled: dict,
              blindspot, rows: list[dict], now: datetime) -> int:
        """检测 3 机会事件, 返回推送成功数。失败不崩。"""
        n_sent = 0
        try:
            n_sent += self._check_board_new(new_entries, pool, rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('board_new 检测异常, 跳过')
        try:
            n_sent += self._check_hot_streak(pool, now)
        except Exception:  # noqa: BLE001
            logger.exception('hot_streak 检测异常, 跳过')
        try:
            n_sent += self._check_limit_up(drilled, blindspot, now)
        except Exception:  # noqa: BLE001
            logger.exception('limit_up 检测异常, 跳过')
        return n_sent

    # ── 新主线 (每轮≤2, 去重) ──
    def _check_board_new(self, new_entries, pool, rows, now) -> int:
        if not new_entries or not self.pub:
            return 0
        # 新入池未推过的板块, 取前 2
        fresh = [c for c in new_entries if c not in self._pushed_new][:2]
        if not fresh:
            return 0
        rmap = {r['code']: r for r in rows}
        boards = []
        for c in fresh:
            r = rmap.get(c)
            if not r:
                continue
            boards.append({
                'name': r.get('name', c), 'zaf': r.get('ZAF', 0),
                'zt': int(r.get('ZTGPNum', 0)),
                'lights': sorted(r.get('searchlights', set()))[:4],
            })
            self._pushed_new.add(c)
        if boards and self.pub.on_board_new(boards, now):
            logger.info('🟢 机会: 新主线 {}', [b['name'] for b in boards])
            return 1
        return 0

    # ── 趋势确认 (rounds_in≥3 且未推过) ──
    def _check_hot_streak(self, pool, now) -> int:
        if not self.pub:
            return 0
        # 池内连续 HOT 达阈值且未推过, 只推最强者
        cands = [s for s in pool.boards.values()
                 if s.state == 'HOT' and s.rounds_in >= cfg.OPP_STREAK_ROUNDS
                 and s.code not in self._pushed_streak]
        if not cands:
            return 0
        best = max(cands, key=lambda s: s.score)
        self._pushed_streak.add(best.code)
        if self.pub.on_hot_streak({
                'name': best.name, 'rounds': best.rounds_in, 'score': best.score,
                'zt_prev': best.zt_num_prev, 'zt_cur': best.zt_num}, now):
            logger.info('🔥 机会: 趋势确认 {}', best.name)
            return 1
        return 0

    # ── 龙头封板 (drilled 涨停, 每轮≤2, 每只只推一次) ──
    def _check_limit_up(self, drilled, blindspot, now) -> int:
        if not drilled or not self.pub:
            return 0
        # 涨停股 (FCAmo>0 且 ZAF≥9) 未推过, 按封单额降序取前 2
        zt = [(c, d) for c, d in drilled.items()
              if d.get('FCAmo', 0) > 0 and d.get('ZAF', 0) >= cfg.OPP_LIMIT_ZAF
              and c not in self._pushed_limit]
        if not zt:
            return 0
        zt.sort(key=lambda kv: kv[1].get('FCAmo', 0), reverse=True)
        top = zt[:2]
        stocks = []
        for c, d in top:
            self._pushed_limit.add(c)
            first_limit = None
            if blindspot and hasattr(blindspot, 'first_limit_time'):
                first_limit = blindspot.first_limit_time.get(c)
            boards = None
            if self.ms:
                boards = [self.ms.board_name(b) for b in
                          sorted(self.ms.boards_of(c))[:3]]
            stocks.append({
                'name': self.ms.stock_name(c) if self.ms else c,
                'zaf': d.get('ZAF', 0), 'fcamo': d.get('FCAmo', 0),
                'fcb': d.get('FCb', 0), 'first_limit': first_limit,
                'boards': boards,
            })
        if stocks and self.pub.on_limit_up(stocks, now):
            logger.info('🚀 机会: 龙头封板 {}',
                        [s['name'] for s in stocks])
            return 1
        return 0


if __name__ == '__main__':
    # 自检: 合成 pool/drilled/blindspot 验 3 事件触发 (dry-run 打印)
    from types import SimpleNamespace as NS
    import publisher as pub_mod

    pub = pub_mod.Publisher(dry_run=True)
    eng = OpportunityEngine(pub=pub, dry_run=True)

    class FakePool:
        def __init__(self):
            self.boards = {
                '880001.SH': NS(code='880001.SH', name='PCB概念', state='HOT',
                                rounds_in=4, score=72.0, zt_num_prev=8, zt_num=11),
            }
    pool = FakePool()
    # drilled: 2 涨停股
    drilled = {
        '300986.SZ': {'FCAmo': 7757.42, 'ZAF': 20.03, 'FCb': 0.06},
        '688020.SH': {'FCAmo': 6609.36, 'ZAF': 20.00, 'FCb': 0.07},
        '600519.SH': {'FCAmo': 0.0, 'ZAF': 0.05, 'FCb': 0.0},
    }
    blindspot = NS(first_limit_time={'300986.SZ': '10:32:05'})
    rows = [{'code': '881334.SH', 'name': 'PCB', 'ZAF': 8.71, 'ZTGPNum': 11,
             'searchlights': {'gain', 'zt'}}]
    now = datetime.now()
    n = eng.check(['881334.SH'], pool, drilled, blindspot, rows, now)
    print(f'\n=== 机会引擎自检: 推送 {n} 张 ===')
    # 去重验证: 再跑一次, 应全部不重推
    n2 = eng.check(['881334.SH'], pool, drilled, blindspot, rows, now)
    print(f'二次调用推送 {n2} 张 (去重应=0)')
    assert n2 == 0, '去重失败, 不应重复推送'
    print('OPPORTUNITY SELF-TEST PASSED')
