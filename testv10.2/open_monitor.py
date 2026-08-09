"""testv10.2 开盘监控 (open_monitor) — 传导链①龙头异动: 开盘快速拉升

脚本路径: K:/QuestDB_test/testv10.2/open_monitor.py
时段: 9:30-9:45 (stage='open')
架构 (方案A, 回调 vs 轮询 COM 并发已盘外模拟验证):
  回调线程: _on_data → signal_q.put(code)   ← 唯一动作, 不碰 tq
  主线程:    process_pending → get_market_snapshot(Open/Now) → 相对开盘>5%
             → unsubscribe_hq + send_warn(客户端) + 飞书卡(bucket)
  → 所有 tq 调用串行在主线程; radar 开盘段主循环快速消化 queue (非 60s sleep)
候选源: start(candidates) 外部注入 (Phase3 先 watchlist; 后续接竞价一字/昨涨停)
接口规范: get_market_snapshot(非get_full_tick) / subscribe_hq≤100 / send_warn 必选参数
红线: dry_run 默认; send_warn 走客户端(不占飞书bucket); 飞书卡 ≤2/min; 失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import json  # noqa: E402
from queue import Queue, Empty  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402
import settings as cfg  # noqa: E402


class OpenMonitor:
    """开盘快速拉升监控: subscribe_hq + queue(方案A) + 主循环串行处理。"""

    def __init__(self, ms=None, pub=None, dry_run: bool | None = None):
        self.ms = ms
        self.pub = pub
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.threshold = cfg.OPEN_SURGE_THRESHOLD
        self.max_sub = cfg.OPEN_MAX_SUB
        self.signal_q: Queue = Queue()
        self.subscribed: set[str] = set()   # 当前订阅 code
        self.fired: set[str] = set()        # 已触发 (去重, 不重复发)
        self.started = False

    # ── 回调 (方案A: 只 queue.put, 不碰 tq) ──
    def _on_data(self, data_str: str) -> None:
        try:
            code = json.loads(data_str).get('Code')
        except Exception:  # noqa: BLE001
            return
        if code:
            self.signal_q.put(code)

    # ── 订阅管理 ──
    def start(self, candidates: list[str]) -> bool:
        """订阅候选 (≤max_sub, 超截断)。candidates 由外部选股注入。"""
        codes = [c for c in candidates if c not in self.subscribed][:self.max_sub]
        if not codes:
            logger.warning('开盘监控: 无候选, 不订阅')
            return False
        r = safe_call(tq.subscribe_hq, stock_list=codes, callback=self._on_data)
        if not r or r.get('ErrorId') != '0':
            logger.error('subscribe_hq 失败: {}', r)
            return False
        self.subscribed.update(codes)
        self.started = True
        logger.info('开盘监控: 订阅 {} 只 (累计 {}), 阈值相对开盘>{}%',
                    len(codes), len(self.subscribed), self.threshold)
        return True

    def stop(self) -> None:
        if self.subscribed:
            safe_call(tq.unsubscribe_hq, stock_list=list(self.subscribed))
            logger.info('开盘监控: 取消订阅 {} 只', len(self.subscribed))
        self.subscribed.clear()
        self.started = False

    # ── 主循环调用: 串行消化 queue ──
    def process_pending(self, max_n: int = None) -> int:
        """radar 主循环 (开盘段快速) 调: 消化所有待处理 code → snapshot+判定+预警。
        返回处理条数。max_n 默认 OPEN_PROCESS_BATCH (防单轮过久)。"""
        max_n = cfg.OPEN_PROCESS_BATCH if max_n is None else max_n
        n = 0
        while n < max_n:
            try:
                code = self.signal_q.get_nowait()
            except Empty:
                break
            try:
                self._handle(code)
            except Exception:  # noqa: BLE001  单股异常不影响其他
                logger.debug('开盘处理 {} 异常', code)
            n += 1
        return n

    def _handle(self, code: str) -> None:
        if code in self.fired:
            return
        snap = safe_call(tq.get_market_snapshot, stock_code=code,
                         field_list=['Now', 'Open', 'LastClose']) or {}
        now_p = float(snap.get('Now') or 0)
        open_p = float(snap.get('Open') or 0)
        rise = ((now_p - open_p) / open_p * 100) if open_p > 0 else 0
        if rise <= self.threshold:
            return
        self.fired.add(code)
        # 触发: unsubscribe (释放名额) + send_warn + 飞书卡
        safe_call(tq.unsubscribe_hq, stock_list=[code])
        self.subscribed.discard(code)
        name = self.ms.stock_name(code) if self.ms else code
        logger.warning('🚀 开盘拉升 {}({}) {} 相对开盘 {:+.1f}%', name, code, now_p, rise)
        self._alert(code, name, now_p, rise, snap.get('LastClose'))

    def _alert(self, code: str, name: str, price: float, rise: float, last_close) -> None:
        # send_warn (客户端预警, 每只都发, 不占飞书 bucket)
        if not self.dry_run:
            safe_call(tq.send_warn,
                      stock_list=[code],
                      time_list=[datetime.now().strftime('%Y%m%d%H%M%S')],
                      price_list=[f'{price:.2f}'],
                      close_list=[str(last_close or 0)],
                      volum_list=['0'], bs_flag_list=['2'],
                      warn_type_list=['0'],
                      reason_list=[f'开盘拉升{rise:.1f}%'],
                      count=1)
        # 飞书卡 (走 bucket ≤2/min, 防刷屏)
        if self.pub:
            self.pub.on_open_surge(code, name, price, rise, datetime.now())


if __name__ == '__main__':
    # 独立验证 (9:30-9:45 盘中跑, 或 --simulate 盘外模拟)
    import sys  # noqa: E402
    import threading  # noqa: E402
    import time  # noqa: E402
    import random  # noqa: E402
    from lib.tq_client import init, close  # noqa: E402
    import publisher as pub_mod  # noqa: E402

    WATCH = ['600519.SH', '300750.SZ', '300059.SZ', '688981.SH', '000725.SZ',
             '601318.SH', '002594.SZ', '600036.SH']
    simulate = '--simulate' in sys.argv

    init()
    try:
        pub = pub_mod.Publisher(dry_run=True)
        mon = OpenMonitor(pub=pub, dry_run=True)
        if simulate:
            # 盘外模拟: 不 subscribe, producer 线程造信号
            STOP = False
            def producer():
                while not STOP:
                    mon.signal_q.put(random.choice(WATCH))
                    time.sleep(0.3)
            threading.Thread(target=producer, daemon=True).start()
            print('[模拟] producer 造信号, 主循环 process_pending 消化 (Ctrl+C 退出)')
        else:
            mon.start(WATCH)
            print(f'已订阅 {len(WATCH)} 只, 主循环消化 (Ctrl+C 退出)')
        while True:
            n = mon.process_pending()
            if n:
                print(f'  处理 {n} 条, 已触发 {len(mon.fired)}, 订阅剩 {len(mon.subscribed)}')
            time.sleep(0.5 if simulate else 1)
    except KeyboardInterrupt:
        print('\n退出')
    finally:
        if not simulate:
            mon.stop()
        close()
