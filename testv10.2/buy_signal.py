"""testv10.2 猎杀模式买入信号 (buy_signal) — 板块涨幅→领涨梯队→龙头/中军/跟风买点

脚本路径: K:/QuestDB_test/testv10.2/buy_signal.py
用途: 把系统从"播报"(每事件推卡) 改成"猎杀"(只在猎物确认时推可扣扳机的靶点)。
      核心链路: 板块涨幅(猎场) → 领涨梯队(猎物) → 买点优先级 龙头(排板)>中军(低吸)>跟风(谨慎)。
      每推一张卡, 都是给交易者一个"现在买什么、为什么买、怎么买"的可执行答案。
原料: board_pool(板块状态) + drilled(个股因子) + blindspot(首封/封单趋势) + ms(映射)
      + sentiment(环境可做性)。纯内存消费, 零新采集。
门控 (猎杀 vs 播报): 板块确认 + 梯队完整 + 强度未衰 + 环境可做 全满足才推, 缺一安静。
买点分级:
  龙头 = 板内连板最高且 FCAmo>0 (封板):  若封单增/主力流入 → 排板 (首位候选)
  中军 = 封单第2大 或 放量未封(涨幅≥7%+换手≥5%): → 低吸 (资金承接, 胜率/赔率最优)
  跟风 = 其余未封但涨幅≥5%:            → 谨慎 (只跟随信号, 不追高)
红线: dry_run 默认; 同板块 5min 冷却; ≤2/min bucket (lane0); 失败不崩 funnel。
"""

import bootstrap
bootstrap.ensure_paths()

import time  # noqa: E402
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


def _trend_pct(blindspot, code: str) -> float:
    """封单变化百分比 (负=衰减)。"""
    if not blindspot or not hasattr(blindspot, 'fcamo_hist'):
        return 0.0
    hist = blindspot.fcamo_hist.get(code, [])
    if len(hist) < 2 or hist[-1] <= 0 or hist[-2] <= 0:
        return 0.0
    return (hist[-1] - hist[-2]) / hist[-2] * 100


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _f(v, default: float = 0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r
    except (TypeError, ValueError):
        return default


class BuySignalEngine:
    """猎杀买入信号引擎: 板块确认 → 梯队提取 → 买点分级 → 冷却去重 → 推卡。"""

    def __init__(self, ms=None, pub=None, dry_run: bool | None = None):
        self.ms = ms
        self.pub = pub
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self._pushed: dict[str, float] = {}   # board_code → 最近推送 ts (同板块冷却)
        self._sent = 0

    def _name(self, code: str) -> str:
        return self.ms.stock_name(code) if self.ms else code

    def _boards(self, code: str, n: int = 3) -> list[str]:
        if not self.ms:
            return []
        return [self.ms.board_name(b) for b in sorted(self.ms.boards_of(code))[:n]]

    # ── 主入口 (radar 盘中/tail 每轮调) ──
    def check(self, pool, drilled: dict, blindspot, rows: list[dict],
              sentiment, now: datetime) -> int:
        """检测猎杀信号, 返回推送成功数。板块确认 → 梯队 → 买点分级 → 冷却。"""
        if not drilled or not self.pub:
            return 0
        n_sent = 0
        try:
            n_sent += self._check_boards(pool, drilled, blindspot, rows,
                                         sentiment, now)
        except Exception:  # noqa: BLE001  失败不崩 funnel
            logger.exception('buy_signal 检测异常, 跳过')
        return n_sent

    # ── 板块维度: 确认猎场 (涨停≥3 + 涨幅≥4% + 动能分≥50) ──
    def _check_boards(self, pool, drilled, blindspot, rows, sentiment, now) -> int:
        # 环境门控: 情绪冰点/跳水时不推买入信号 (大环境不能做)
        env_ok = self._env_ok(sentiment)
        if not env_ok:
            logger.debug('buy_signal: 情绪环境不可做, 本轮安静')
            return 0

        rmap = {r['code']: r for r in rows}
        n_sent = 0
        # 池内 HOT/NEW 板块 (按动能分降序, 优先强板)
        for st in sorted((s for s in pool.snapshot() if s.state in ('HOT', 'NEW')),
                         key=lambda s: s.score, reverse=True):
            if n_sent >= 2:   # 每轮最多 2 张 (猎杀宁缺毋滥)
                break
            r = rmap.get(st.code, {})
            # 板块确认: 涨停≥3 + 涨幅≥4% + 动能分≥50
            zt = _int(r.get('ZTGPNum', 0))
            zaf = _f(r.get('ZAF', 0))
            if not (zt >= cfg.HUNT_SECTOR_ZT and zaf >= cfg.HUNT_SECTOR_ZAF
                    and _f(st.score) >= cfg.HUNT_MIN_SCORE):
                continue
            # 冷却: 同板块 5min 不重复推
            if now.timestamp() - self._pushed.get(st.code, 0) < cfg.HUNT_COOLDOWN_SEC:
                continue
            # 梯队提取 + 买点分级 (核心)
            sig = self._build_signal(st, drilled, blindspot, now)
            if not sig or not sig['targets']:
                continue
            # 强度门控: 龙头封单衰减 → 不推 (别追正在松动的板)
            if sig['leader_fade']:
                logger.debug('buy_signal: {} 龙头封单衰减, 不推', sig['board'])
                continue
            self._pushed[st.code] = now.timestamp()
            if self.pub.on_buy_signal(sig, now):
                self._sent += 1
                n_sent += 1
                logger.info('🎯 猎杀信号: {} 龙头{} 中军{} 跟风{}',
                            sig['board'], sig['leader_name'], sig['mid_name'],
                            len(sig['follows']))
        return n_sent

    # ── 环境门控 (大环境能不能做) ──
    def _env_ok(self, sentiment) -> bool:
        try:
            res = sentiment.last_result if sentiment else None
            if not res:
                return True
            score = _f(res.get('score', 50))
            return score >= 40   # 冰点 (<40) 不推买入信号
        except Exception:  # noqa: BLE001
            return True

    # ── 梯队提取 + 买点分级 (核心) ──
    def _build_signal(self, st, drilled, blindspot, now) -> dict | None:
        bc = st.code
        # 板内成分股 (drilled 中属于该板的)
        bst = {c: d for c, d in drilled.items()
               if self.ms and bc in self.ms.boards_of(c)}
        if len(bst) < 2:
            return None
        # 梯队完整性门控: 红盘率≥50% (板块真涨, 非个别股硬拉)
        red = sum(1 for d in bst.values() if _f(d.get('ZAF', 0)) > 0)
        if red / len(bst) < cfg.HUNT_BOARD_RED_RATIO:
            return None

        # 板内排序: 连板高 > 封板 > 首封早 > 封单大 (梯队: 龙头/中军/跟风)
        def _key(item):
            c, d = item
            fl = blindspot.first_limit_time.get(c, '') if blindspot else ''
            return (_f(d.get('EverZTCount', 0)),
                    1 if _f(d.get('FCAmo', 0)) > 0 else 0,
                    fl, _f(d.get('FCAmo', 0)))
        ranked = sorted(bst.items(), key=_key, reverse=True)

        # ── 龙头: 连板最高 + 封板 (真龙头) ──
        leader = None
        for c, d in ranked:
            if _f(d.get('FCAmo', 0)) > 0 and _f(d.get('EverZTCount', 0)) >= cfg.HUNT_LB:
                leader = (c, d)
                break
        if not leader:   # 无 ≥2 连板龙头 → 不成猎场 (首板不算真龙头)
            return None
        lc, ld = leader
        lb = _int(ld.get('EverZTCount', 0))
        fc = _f(ld.get('FCAmo', 0))
        fl = blindspot.first_limit_time.get(lc, '') if blindspot else ''
        zjl = _f(ld.get('Zjl_HB', 0))
        zjl_s = f'主力{int(zjl/1e4)}亿' if abs(zjl) > 0 else ''
        # 龙头封单衰减门控: 封单降幅≥15% → 别追
        fade_pct = _trend_pct(blindspot, lc)
        fade = fade_pct <= -cfg.HUNT_FADE_MIN
        trend_s = _fcamo_trend(blindspot, lc)
        # 龙头买点: 封单增/主力流入 → 排板 (首位候选)
        leader_action = '排板' if (fade_pct >= 0 or zjl >= cfg.HUNT_ZJIN) else '排队'

        # ── 中军: 封单第2大 (板内次强封板) 或 放量未封 (涨幅≥7%+换手≥5%) ──
        mid = None
        # 1) 次强封板 (封单>0 的第2大)
        sealed = [(c, d) for c, d in ranked if _f(d.get('FCAmo', 0)) > 0
                  and c != lc]
        if sealed:
            mid = sealed[0]
        else:
            # 2) 放量未封: 涨幅≥7% + 换手≥5% (资金承接核心票)
            for c, d in ranked:
                if (c != lc and _f(d.get('FCAmo', 0)) <= 0
                        and _f(d.get('ZAF', 0)) >= cfg.HUNT_KEDA_ZAF
                        and _f(d.get('fHSL', 0)) >= cfg.HUNT_KEDA_FHSL):
                    mid = (c, d)
                    break
        mid_s = ''
        mid_action = ''
        if mid:
            mc, md = mid
            m_zaf = _f(md.get('ZAF', 0))
            m_fc = _f(md.get('FCAmo', 0))
            m_hsl = _f(md.get('fHSL', 0))
            if m_fc > 0:
                mid_s = f'{self._name(mc)}({m_zaf:+.1f}%/封{m_fc:.0f}万)'
                mid_action = '低吸'   # 中军次强封板 → 低吸 (资金承接)
            else:
                mid_s = f'{self._name(mc)}({m_zaf:+.1f}%/{m_hsl:.0f}%换)'
                mid_action = '低吸'   # 放量未封 → 低吸 (上车点)

        # ── 跟风: 其余未封但涨幅≥5% (跟随信号, 谨慎) ──
        follows = []
        for c, d in ranked:
            if (c != lc and (mid is None or c != mid[0])
                    and _f(d.get('FCAmo', 0)) <= 0
                    and _f(d.get('ZAF', 0)) >= 5):
                follows.append(self._name(c))
                if len(follows) >= 2:
                    break
        if not follows:   # 无跟风 → 独角戏, 不成猎场
            return None

        # 跟风数门控: 梯队完整 (至少 2 只跟风) — 呼应 cfg.HUNT_FOLLOW_N
        if len(follows) < cfg.HUNT_FOLLOW_N:
            return None

        # 可打: 中军 + 跟风里 未封 涨幅≥7% 的 1-2 只 (给交易者具体扣扳机标的)
        keda = []
        for c, d in ranked:
            if (c != lc and _f(d.get('FCAmo', 0)) <= 0
                    and _f(d.get('ZAF', 0)) >= cfg.HUNT_KEDA_ZAF
                    and _f(d.get('fHSL', 0)) >= cfg.HUNT_KEDA_FHSL
                    and len(keda) < cfg.HUNT_KEDA_MAX):
                keda.append(f'{self._name(c)}({_f(d.get("ZAF", 0)):+.1f}%/{_f(d.get("fHSL", 0)):.0f}%换)')

        return {
            'board': (st.name if st.name else st.code),
            'zt': _int(self._zt_of(st)),
            'board_zaf': _f(self._zaf_of(st)),
            'leader_name': self._name(lc),
            'leader_lb': lb,
            'leader_fc': fc,
            'leader_fl': fl,
            'leader_trend': trend_s,
            'leader_zjl': zjl_s,
            'leader_action': leader_action,
            'leader_fade': fade,
            'mid_name': mid_s,
            'mid_action': mid_action,
            'follows': follows,
            'keda': keda,
            'board_code': bc,
        }

    # helper 占位 (调用方经 rmap 取数, 这里用 st 自身字段)
    def _zt_of(self, st) -> int:
        return _int(getattr(st, 'zt_num', 0))

    def _zaf_of(self, st) -> float:
        return _f(getattr(st, 'zaf', 0))

    def _boards_of(self, code: str) -> list[str]:
        return self._boards(code)


if __name__ == '__main__':
    # 自检: 合成 pool/drilled/blindspot 验 买点分级 + 门控 (dry-run 打印)
    from types import SimpleNamespace as NS
    import publisher as pub_mod

    pub = pub_mod.Publisher(dry_run=True)
    eng = BuySignalEngine(pub=pub, dry_run=True)

    class FakeMs:
        def stock_name(self, c): return {'A': '龙头A', 'B': '中军B', 'C': '跟风C', 'D': '跟风D'}.get(c, c)
        def boards_of(self, c): return {'880001.SH'}
        def board_name(self, b): return '创新药'

    eng.ms = FakeMs()

    class FakePool:
        def snapshot(self):
            return [NS(code='880001.SH', name='创新药', state='HOT', score=72.0,
                       zt_num=5, zaf=5.5)]

    # 板块确认: 涨停5 + 涨幅5.5% + 动能分72 → 触发
    # 梯队: A=2连板龙头(封单8000万/主力1.5亿) B=封单5000万(中军) C/D=未封涨幅8/6%
    # 单位: Zjl_HB 万元 (探针 2026-08-10 实测), 1.5e4万=1.5亿
    drilled = {
        'A': {'EverZTCount': 2, 'FCAmo': 8000, 'ZAF': 10, 'fHSL': 8, 'Zjl_HB': 1.5e4},
        'B': {'EverZTCount': 1, 'FCAmo': 5000, 'ZAF': 10, 'fHSL': 9, 'Zjl_HB': 5e3},
        'C': {'EverZTCount': 1, 'FCAmo': 0, 'ZAF': 8.5, 'fHSL': 6},
        'D': {'EverZTCount': 1, 'FCAmo': 0, 'ZAF': 6.2, 'fHSL': 4},
    }
    blindspot = NS(first_limit_time={'A': '09:31:00'},
                   fcamo_hist={'A': [8000, 9000]},   # 封单在增
                   break_count={}, back_count={}, zt_count={})
    rows = [{'code': '880001.SH', 'name': '创新药', 'ZTGPNum': 5, 'ZAF': 5.5}]
    now = datetime.now()
    sig = eng._build_signal(FakePool().snapshot()[0], drilled, blindspot, now)
    assert sig, '强板应触发猎杀信号'
    assert sig['leader_name'] == '龙头A' and sig['leader_lb'] == 2
    assert sig['leader_action'] == '排板', f'封单增+主力流入应排板, 实际 {sig["leader_action"]}'
    assert sig['mid_name'], '应有中军'
    assert sig['leader_fade'] is False, '封单在增不应衰减'
    print(f"信号: 板块{sig['board']}(涨{sig['board_zaf']:+.1f}% 涨停{sig['zt']})")
    print(f"  龙头 {sig['leader_name']} {sig['leader_lb']}连板 封单{sig['leader_fc']:.0f}万 "
          f"{sig['leader_trend']} {sig['leader_zjl']} → {sig['leader_action']}")
    print(f"  中军 {sig['mid_name']} → {sig['mid_action']}")
    print(f"  跟风 {sig['follows']}")
    if sig['keda']:
        print(f"  可打 {' '.join(sig['keda'])}")

    # 门控验证 1: 无 ≥2 连板龙头 → 不触发 (首板不算真龙头)
    drilled2 = dict(drilled)
    drilled2['A'] = dict(drilled['A'], EverZTCount=1)
    sig2 = eng._build_signal(FakePool().snapshot()[0], drilled2, blindspot, now)
    assert sig2 is None, '无2连板龙头不成猎场, 应 None'
    print('门控1 无2连板龙头 → 安静 ✅')

    # 门控验证 2: 龙头封单衰减 → 不推
    bs2 = NS(first_limit_time={'A': '09:31:00'},
             fcamo_hist={'A': [9000, 6000]},   # 封单在减
             break_count={}, back_count={}, zt_count={})
    sig3 = eng._build_signal(FakePool().snapshot()[0], drilled, bs2, now)
    assert sig3['leader_fade'] is True, '封单衰减应标记'
    print('门控2 龙头封单衰减 → 标记不推 ✅')
    print('BUY SIGNAL SELF-TEST PASSED')
