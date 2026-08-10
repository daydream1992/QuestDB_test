"""testv10.2 盲区补盲监控 (blindspot) — subscribe_hq 对 TopN 高频感知

脚本路径: K:/QuestDB_test/testv10.2/blindspot_monitor.py
时段: intraday/tail (9:45-15:00) — 60s 轮询盲区内, 订阅池内 TopN 个股高频感知
架构 (方案A 同款, 回调不碰 COM): subscribe_hq 回调 → signal_q.put(code) 唯一动作;
      主循环 process_pending → get_more_info 判 FCAmo 状态变更 (涨停→炸板/回封) 串行消费。
覆盖: 60s 轮询间隔内约 36s 盲区, Top20 感知间隔从 60s 缩到 ~6s (回调频率)。
接口规范: subscribe_hq≤100 (Top20 远在内) / unsubscribe_hq 每轮换列表 / 回调 datas 含 Code。
红线: 回调线程零 COM; 每轮钻取结束后换订 Top20 (unsubscribe 旧 + subscribe 新); 失败不崩 funnel。
"""

import bootstrap
bootstrap.ensure_paths()

import json  # noqa: E402
import time  # noqa: E402
from queue import Queue, Empty  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402
import settings as cfg  # noqa: E402


def _classify(prev: float, cur: float) -> str | None:
    """FCAmo 状态变更判定: 封(>0)→开(≤0)=炸板; 开(≤0)→封(>0)=回封; 否则 None。"""
    if prev > 0 and cur <= 0:
        return '炸板'
    if prev <= 0 and cur > 0:
        return '回封'
    return None


class BlindspotMonitor:
    """盘中盲区补盲: subscribe TopN → 回调入队 → 串行判 FCAmo 状态变更。

    状态变更 (涨停→炸板 / 炸板→回封) 是 60s 轮询最易漏的盘口信号。
    主循环每轮结束后 refresh() 换订最新 TopN; 两轮之间 process_pending() 消化回调。
    """

    def __init__(self, ms=None, dry_run: bool | None = None):
        self.ms = ms
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.signal_q: Queue = Queue(maxsize=256)   # 回调队列上限 (满则丢, 防主动行情积压内存)
        self.subscribed: set[str] = set()
        self.prev_fcamo: dict[str, float] = {}   # 跨轮 code → 上轮 FCAmo (状态变更判定)
        self.fcamo_hist: dict[str, list] = {}    # code → 最近 FCAmo 序列 (封单衰竭检测)
        self.first_limit_time: dict[str, str] = {}  # code → 首次封板时间 HH:MM:SS (回调6s精度, 自建)
        self.zt_count: dict[str, int] = {}       # code → 封板次数 (每次 开→封 累计, 含首次+回封)
        self.break_count: dict[str, int] = {}    # code → 炸板次数 (每次 封→开 累计)
        self.back_count: dict[str, int] = {}     # code → 回封次数 (炸板后再次封住累计)
        self.push_count: dict[str, int] = {}     # code → 炸板/回封推卡次数 (限2次, 防刷屏)
        self.fade_ts: dict[str, float] = {}      # code → 最近衰竭时间戳 (炸板互斥: 衰竭30min内不推炸板卡)
        self._fail_n = 0                          # 订阅连续失败计数 (熔断用)
        self._backoff_until = 0.0                 # 熔断截止时间
        self.events: list[dict] = []             # 本轮累计状态变更事件 (供推送)
        self.started = False

    # ── 回调 (方案A: 只 queue.put, 不碰 tq) ──
    def _on_data(self, data_str: str) -> None:
        try:
            code = json.loads(data_str).get('Code')
        except Exception:  # noqa: BLE001
            return
        if code:
            # 队列上限 (maxsize=256): 满则丢 (20 个去重 code 丢重复无信息损失)
            try:
                self.signal_q.put_nowait(code)
            except Exception:  # noqa: BLE001  Queue.Full
                pass

    # ── 订阅管理 (每轮换订 TopN; ≤max_sub) ──
    def refresh(self, top_codes: list[str]) -> None:
        """换订 TopN: 先退旧 (本轮不在 Top 的), 再补新 (新进 Top 的)。≤100 上限。
        熔断: 连续 3 次失败 → 退避 300s 不试 (防盘中 DLL 通道故障时每轮重试风暴)。"""
        # 熔断: 退避期内直接返回, 不碰 DLL
        if time.time() < self._backoff_until:
            return
        want = [c for c in top_codes if c not in self.subscribed][:cfg.OPEN_MAX_SUB]
        drop = [c for c in self.subscribed if c not in top_codes]
        if drop:
            safe_call(tq.unsubscribe_hq, stock_list=drop)
            for c in drop:
                self.subscribed.discard(c)
                # 掉池清 prev_fcamo (防回来时旧封板状态误判炸板) + fcamo_hist (防内存累积);
                # 计数 (zt/break/back/first_limit) 保留 = "今日累计至今" 语义
                self.prev_fcamo.pop(c, None)
                self.fcamo_hist.pop(c, None)
        if want:
            r = safe_call(tq.subscribe_hq, stock_list=want, callback=self._on_data)
            if r and r.get('ErrorId') == '0':
                self.subscribed.update(want)
                self.started = True
                self._fail_n = 0
            else:
                self._fail_n += 1
                if self._fail_n >= 3:
                    self._backoff_until = time.time() + cfg.SUBSCRIBE_BACKOFF_SEC
                    logger.error('blindspot subscribe 连续{}次失败, 熔断 {}s (降级轮询)',
                                 self._fail_n, cfg.SUBSCRIBE_BACKOFF_SEC)
                else:
                    logger.error('blindspot subscribe_hq 失败 ({}次): {}', self._fail_n, r)
        if not drop and not want and self.subscribed:
            logger.debug('blindspot: TopN 未变化, 保持订阅 {}', len(self.subscribed))

    def stop(self) -> None:
        if self.subscribed:
            safe_call(tq.unsubscribe_hq, stock_list=list(self.subscribed))
            logger.info('盲区补盲: 取消订阅 {} 只', len(self.subscribed))
        self.subscribed.clear()
        self.started = False

    # ── 主循环调用: 串行消化 queue (回调只在盲区内触发, 不占主循环时间) ──
    def process_pending(self, max_n: int = 200, time_budget: float = 5.0) -> int:
        """消化回调队列 → 单只 get_more_info 判 FCAmo 状态变更。返回处理数。

        时间预算优先: 主动行情回调积压时 (Top20 高频回调) 排空队列而非只取 max_n,
        避免"6s 补盲"退化。time_budget 防止单轮处理过久拖累 funnel。"""
        n = 0
        t0 = time.time()
        while n < max_n and time.time() - t0 < time_budget:
            try:
                code = self.signal_q.get_nowait()
            except Empty:
                break
            try:
                self._check(code)
            except Exception:  # noqa: BLE001  单股异常不影响其他
                logger.debug('盲区处理 {} 异常', code)
            n += 1
        return n

    def _check(self, code: str) -> None:
        if code not in self.subscribed:
            return
        mi = safe_call(tq.get_more_info, stock_code=code, field_list=[], timeout=cfg.MOREINFO_TIMEOUT) or {}
        fcamo = float(mi.get('FCAmo') or 0)
        prev = self.prev_fcamo.get(code)
        self.prev_fcamo[code] = fcamo
        # 通用个股字段 (events 携带, 卡片显示涨幅/换手/连板)
        zaf = float(mi.get('ZAF') or 0)
        fhsl = float(mi.get('fHSL') or 0)
        ever = int(float(mi.get('EverZTCount') or 0))
        # 封单衰竭检测 (炸板前兆): 连续 2 轮 FCAmo 降≥40% 且仍>0
        if fcamo > 0 and prev is not None and prev > 0:
            hist = self.fcamo_hist.setdefault(code, [])
            hist.append(fcamo)
            if len(hist) >= 3 and hist[-1] <= hist[-2] * 0.6 and hist[-2] <= hist[-3] * 0.6:
                self.fade_ts[code] = time.time()   # 记衰竭时间 (炸板互斥用)
                self.events.append({'code': code, 'name': self.ms.stock_name(code) if self.ms else code,
                                    'type': '衰竭', 'prev': prev, 'cur': fcamo,
                                    'zaf': zaf, 'fHSL': fhsl, 'ever_zt': ever})
                hist.clear()   # 已报一次, 防重复
                logger.warning('⚠️ 封单衰竭 {} ({}) {:.0f}→{:.0f}', code, code, prev, fcamo)
        if prev is None:
            # 基线: 订阅建立时已封的股, 记 1 次封板 (开板前就封, 算基线)
            # 注意: 不记 first_limit_time — 基线时刻是订阅建立(9:45), 非真实首封,
            # 记了会污染"前排原则"判断 (把 9:31 封死的龙头标成 9:45 首封).
            if fcamo > 0:
                self.zt_count[code] = self.zt_count.get(code, 0) + 1
                logger.info('⏱ 基线封板 {} ({}) (订阅时已封, 不计首封时间)',
                            self.ms.stock_name(code) if self.ms else code, code)
            return
        # 状态变更: 封→开=炸板; 开→封 (今日首次封板 vs 回封)
        kind = _classify(prev, fcamo)
        if not kind:
            return
        name = self.ms.stock_name(code) if self.ms else code
        # 开→封 区分: 今日从未封过 (无首次封板记录) = 封板, 否则 = 回封
        is_first_limit = (kind == '回封' and code not in self.first_limit_time
                          and self.zt_count.get(code, 0) == 0)
        event_type = '封板' if is_first_limit else kind
        # 推卡限次: 同股炸板/回封合计 >2 次后, 事件标记 no_card (只落表不推卡, 防反复刷)
        pn = self.push_count.get(code, 0)
        no_card = (event_type in ('炸板', '回封') and pn >= 2)
        if event_type in ('炸板', '回封'):
            self.push_count[code] = pn + 1
        self.events.append({'code': code, 'name': name, 'type': event_type,
                            'prev': prev, 'cur': fcamo,
                            'zaf': zaf, 'fHSL': fhsl, 'ever_zt': ever,
                            'no_card': no_card})
        # 计数: 封板(开→封, 含首次) / 炸板(封→开) / 回封(炸板后再次封)
        if kind == '回封':
            self.zt_count[code] = self.zt_count.get(code, 0) + 1
            if is_first_limit:
                # 首次封板时间戳 (自建): 开→封 那一刻记录 (回调~6s精度, 前排原则)
                self.first_limit_time[code] = datetime.now().strftime('%H:%M:%S')
                logger.info('⏱ 首次封板 {} ({}) {}', name, code,
                            self.first_limit_time[code])
            else:
                self.back_count[code] = self.back_count.get(code, 0) + 1
        elif kind == '炸板':
            self.break_count[code] = self.break_count.get(code, 0) + 1
        logger.warning('💥 盲区补盲: {} ({}) {} (封单 {:.0f}→{:.0f})',
                       name, code, '炸板' if kind == '炸板' else '回封', prev, fcamo)

    def drain_events(self) -> list[dict]:
        """取走本轮累积的状态变更事件 (供 alert_engine/推送消费), 并清空。"""
        ev = self.events
        self.events = []
        return ev


if __name__ == '__main__':
    # 自检: _classify 状态变更判定 (纯逻辑, 不依赖 COM)
    assert _classify(5000.0, 0.0) == '炸板'
    assert _classify(5000.0, -1.0) == '炸板'
    assert _classify(0.0, 3000.0) == '回封'
    assert _classify(0.0, 0.0) is None
    assert _classify(5000.0, 6000.0) is None
    print('_classify 全边界通过: 炸板(封→开) / 回封(开→封) / 无变更(保持)')
    # drain_events 清空语义
    mon = BlindspotMonitor(dry_run=True)
    mon.events.append({'code': '688020.SH', 'name': '方邦股份', 'type': '炸板',
                       'prev': 6609.0, 'cur': 0.0})
    ev = mon.drain_events()
    assert len(ev) == 1 and ev[0]['type'] == '炸板'
    assert mon.drain_events() == [], 'drain 后应清空'
    print('drain_events 累积+清空通过')
    print('BLINDSPOT SELF-TEST PASSED')
