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


def _fcamo_trend(blindspot, code: str) -> str:
    """封单变化率: 从 blindspot.fcamo_hist 最近两值算趋势 (增/减/平)。"""
    if not blindspot or not hasattr(blindspot, 'fcamo_hist'):
        return ''
    hist = blindspot.fcamo_hist.get(code, [])
    if len(hist) < 2 or hist[-1] <= 0:
        return ''
    prev, cur = hist[-2], hist[-1]
    if prev <= 0:
        return ''
    pct = (cur - prev) / prev * 100
    if pct >= 10:
        return f'封单+{pct:.0f}%'
    if pct <= -10:
        return f'封单{pct:.0f}%'
    return '封单平'


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
            n_sent += self._check_board_new(new_entries, pool, rows, drilled,
                                            blindspot, now)
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

    # ── 新主线 (每轮≤2, 去重, 收紧: 只推强板防骚扰) ── 猎场卡: 板块+龙头+可打
    def _check_board_new(self, new_entries, pool, rows, drilled, blindspot, now) -> int:
        if not new_entries or not self.pub:
            return 0
        rmap = {r['code']: r for r in rows}
        # 收紧: 只推 动能分≥50 且 涨停≥2 的强板 (防 28 次/天骚扰, 呼应 agent P0-3)
        strong = [c for c in new_entries
                  if rmap.get(c, {}).get('score', 0) >= cfg.OPP_NEW_SCORE
                  and int(rmap.get(c, {}).get('ZTGPNum', 0)) >= cfg.OPP_NEW_ZT
                  and c not in self._pushed_new]
        fresh = strong[:cfg.OPP_NEW_MAX]
        if not fresh:
            return 0
        # 板块→成分股 (drilled 内该板块的个股)
        def _board_stocks(bc):
            if not self.ms:
                return {}
            return {c: d for c, d in drilled.items()
                    if bc in self.ms.boards_of(c) and c in drilled}
        boards = []
        for c in fresh:
            r = rmap.get(c)
            if not r:
                continue
            bst = _board_stocks(c)
            # 龙头: 板块内 FCAmo>0 且连板最高 / 封单最大
            leader = None
            for sc, sd in bst.items():
                if sd.get('FCAmo', 0) > 0:
                    if leader is None or (sd.get('EverZTCount', 0) >
                                          leader['sd'].get('EverZTCount', 0)):
                        leader = {'code': sc, 'sd': sd}
            leader_s = ''
            if leader:
                nm = self.ms.stock_name(leader['code']) if self.ms else leader['code']
                lb = int(leader['sd'].get('EverZTCount', 0))
                lb_s = f'{lb}连板' if lb >= 2 else '首板'
                fl = blindspot.first_limit_time.get(leader['code'], '') if blindspot else ''
                fl_s = f' ⏱{fl}' if fl else ''
                leader_s = f'龙头:{nm}({lb_s}/封单{leader["sd"].get("FCAmo",0):.0f}万){fl_s}'
            # 可打: 板块内未封但涨幅≥7% 的 1-2 只 (启动/补涨, 呼应"不推已涨停")
            keda = []
            for sc, sd in sorted(bst.items(), key=lambda kv: -kv[1].get('ZAF', 0)):
                if sd.get('FCAmo', 0) <= 0 and sd.get('ZAF', 0) >= 7 and len(keda) < 2:
                    nm = self.ms.stock_name(sc) if self.ms else sc
                    keda.append(f'{nm}({sd.get("ZAF",0):.1f}%/{sd.get("fHSL",0):.0f}%换)')
            boards.append({
                'name': r.get('name', c), 'zaf': r.get('ZAF', 0),
                'zt': int(r.get('ZTGPNum', 0)),
                'leader': leader_s,
                'keda': '可打:' + ' '.join(keda) if keda else '',
            })
        if boards:
            # 无论推送成败都去重 (限频丢弃也标记, 防反复刷)
            self._pushed_new.update(c for c in fresh if c in rmap)
            if self.pub.on_board_new(boards, now):
                logger.info('🟢 机会: 新主线 {}', [b['name'] for b in boards])
                return 1
        return 0

    # ── 趋势确认 (rounds_in≥3 且未推过) ──
    def _check_hot_streak(self, pool, now) -> int:
        if not self.pub:
            return 0
        # 趋势确认停推 (2026-08-10): 状态达成非瞬时事件, 推送骚扰, 只保留检测维护跨轮状态
        return 0
        # 池内连续 HOT 达阈值且未推过, 只推最强者
        cands = [s for s in pool.boards.values()
                 if s.state == 'HOT' and s.rounds_in >= cfg.OPP_STREAK_ROUNDS
                 and s.code not in self._pushed_streak]
        if not cands:
            return 0
        best = max(cands, key=lambda s: s.score)
        # 无论推送成败都去重 (限频丢弃也标记, 防反复刷)
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
        # 龙头排序 (前排原则): 首封时间早 > 连板高 > 封单大
        # (首封时间缺失的排后, 连板 EverZTCount 已在 drilled)
        def _sort_key(item):
            c, d = item
            fl = None
            if blindspot and hasattr(blindspot, 'first_limit_time'):
                fl = blindspot.first_limit_time.get(c)
            return (0 if fl else 1, fl or '', -d.get('EverZTCount', 0),
                    -d.get('FCAmo', 0))
        zt.sort(key=_sort_key)
        top = zt[:cfg.OPP_LIMIT_MAX]
        stocks = []
        for c, d in top:
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
                'ever_zt': int(d.get('EverZTCount', 0)),   # 连板高度 (龙头价值核心)
                'fHSL': d.get('fHSL', 0),                  # 换手 (一字/换手板)
                'pos_ratio': d.get('pos_ratio', 0),        # 位置 (高位风险)
                'zjl_hb': d.get('Zjl_HB', 0),              # 主力净流入
                'break_n': blindspot.break_count.get(c, 0) if blindspot else 0,  # 炸板次数(烂板)
                # 封单变化率 (fcamo_hist 最近两值, 封单在增=可打/在减=别追)
                'fc_trend': _fcamo_trend(blindspot, c),
            })
        if stocks:
            # 无论推送成败都去重: 限频丢弃也标记已推, 防同一批涨停股反复刷 (盘中混乱主因)
            self._pushed_limit.update(c for c, _ in top)
            if self.pub.on_limit_up(stocks, now):
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
    blindspot = NS(first_limit_time={'300986.SZ': '10:32:05'},
                   break_count={}, back_count={}, zt_count={})
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
