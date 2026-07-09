"""subscribe: 通达信实时订阅模块

用途: subscribe_hq + get_full_tick 实时推送模式, 写 qd_stock_snapshot 和 qd_stock_intraday
用法:
    from compute.subscribe import Subscriber
    sub = Subscriber()
    sub.add('002747.SZ', '埃斯顿')
    sub.add('002008.SZ', '大族激光')
    sub.start()  # 阻塞, Ctrl+C 退出
"""
import json
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from dotenv import load_dotenv
_ENV_PATH = os.path.join(_PROJ_ROOT, 'config', '.env')
load_dotenv(_ENV_PATH)

_TQCENTER_PATH = os.getenv('TQCENTER_PATH') or r'K:\txdlianghua\PYPlugins\sys'
if _TQCENTER_PATH and _TQCENTER_PATH not in sys.path:
    sys.path.insert(0, _TQCENTER_PATH)

from tqcenter import tq
from lib.tq_client import safe_call, init as tq_init
from lib.qdb import connect, executemany_batch
from loguru import logger


# qd_stock_snapshot 列 (20 个关键字段)
_SNAP_COLS = [
    'snapshot_time', 'code',
    'Now', 'LastClose', 'Open', 'Max', 'Min',
    'Volume', 'Amount', 'NowVol',
    'Inside', 'Outside',
    'Buyv1', 'Buyv2', 'Buyv3', 'Sellv1', 'Sellv2', 'Sellv3',
    'ZAF', 'ItemNum',
]

# qd_stock_intraday 列 (主力资金字段)
_INTRA_COLS = [
    'snapshot_time', 'code',
    'ZAF', 'ZTPrice', 'DTPrice', 'fHSL', 'fLianB',
    'FzAmo', 'Zjl', 'Fzhsl', 'FCAmo', 'FCb', 'vzangsu',
]


class Subscriber:
    """通达信实时订阅管理器

    用法:
        sub = Subscriber()
        sub.add('002747.SZ', '埃斯顿')
        sub.add('002008.SZ', '大族激光')
        sub.start()
    """

    def __init__(self):
        self._targets: dict[str, str] = {}  # code → name
        self._exit_flag = False
        # 连接复用: 每 tick 新建连接耗尽资源，改用复用连接 + 按需重建
        self._con = None
        self._con_mtime = 0.0  # 连接创建时间
        self._con_idle = 0     # 连接空闲秒数
        self._CON_MAX_IDLE = 30  # 最大空闲秒数，超过则重建连接（防 QuestDB idle timeout）
        signal.signal(signal.SIGINT, self._signal_handler)
        if os.name == 'nt':
            signal.signal(signal.SIGBREAK, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def add(self, code: str, name: str = ''):
        """添加订阅标的"""
        self._targets[code] = name or code
        logger.info('订阅添加: {} ({})', code, self._targets[code])

    def remove(self, code: str):
        """取消订阅"""
        if code in self._targets:
            del self._targets[code]
            try:
                tq.unsubscribe_hq(stock_list=[code])
                logger.info('取消订阅: {}', code)
            except Exception:
                pass

    def _get_con(self):
        """获取复用连接（自动按需重建，防止 idle timeout）"""
        now = time.time()
        if self._con is not None:
            # 检查空闲超时
            if now - self._con_mtime > self._CON_MAX_IDLE:
                try:
                    self._con.close()
                except Exception:
                    pass
                self._con = None
        if self._con is None:
            self._con = connect()
            self._con_mtime = now
        return self._con

    def _signal_handler(self, signum, frame):
        logger.info('收到退出信号, 清理订阅...')
        self._exit_flag = True
        try:
            tq.unsubscribe_hq(stock_list=list(self._targets.keys()))
        except Exception:
            pass
        self._cleanup()
        sys.exit(0)

    def on_data(self, data_str: str):
        """订阅回调 — 收到推送后 get_market_snapshot 取完整数据写入 DB"""
        from datetime import time as dtime
        try:
            # 非交易时段直接丢弃推送 (tqcenter 午休会重复推最后一帧)
            now = datetime.now()
            if not (dtime(9, 15) <= now.time() < dtime(11, 30) or
                    dtime(13, 0) <= now.time() < dtime(15, 0)):
                return
            parsed = json.loads(data_str)
            if parsed.get('ErrorId') != '0':
                return
            code = parsed.get('Code')
            if code not in self._targets:
                return

            # 取最新完整行情 (get_market_snapshot 无字段过滤)
            tick = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[])
            if not tick:
                return

            # 补取主力字段 (get_more_info intraday 模式)
            more = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}

            ts = datetime.now()
            name = self._targets.get(code, code)

            # 计算涨幅和缺口
            now_p = float(tick.get('Now', 0) or 0)
            lc = float(tick.get('LastClose', 0) or 0)
            zaf = tick.get('ZAF', 0) or 0
            gap_pct = ((now_p - lc) / lc * 100) if lc > 0 else 0

            logger.info('[订阅] {}({}) Now={:.2f} ZAF={}  Gap={:+.2f}%  Vol={}  Buyv1={}  Sellv1={}  Zjl={}  FCAmo={}',
                        name, code, now_p, zaf, gap_pct,
                        tick.get('Volume'), tick.get('Buyv1'), tick.get('Sellv1'),
                        more.get('Zjl'), more.get('FCAmo'))

            # 写 qd_stock_snapshot + qd_stock_intraday (复用连接)
            con = self._get_con()
            try:
                snap_row = (
                    ts, code,
                    tick.get('Now'), tick.get('LastClose'),
                    tick.get('Open'), tick.get('Max'), tick.get('Min'),
                    tick.get('Volume'), tick.get('Amount'), tick.get('NowVol'),
                    tick.get('Inside'), tick.get('Outside'),
                    tick.get('Buyv1'), tick.get('Buyv2'), tick.get('Buyv3'),
                    tick.get('Sellv1'), tick.get('Sellv2'), tick.get('Sellv3'),
                    tick.get('ZAF'), tick.get('ItemNum'),
                )
                executemany_batch(con, 'qd_stock_snapshot', _SNAP_COLS, [snap_row])

                intra_row = (
                    ts, code,
                    more.get('ZAF'), more.get('ZTPrice'), more.get('DTPrice'),
                    more.get('fHSL'), more.get('fLianB'),
                    more.get('FzAmo'), more.get('Zjl'), more.get('Fzhsl'),
                    more.get('FCAmo'), more.get('FCb'), more.get('vzangsu'),
                )
                executemany_batch(con, 'qd_stock_intraday', _INTRA_COLS, [intra_row])
            finally:
                self._con_mtime = time.time()

        except Exception as e:
            logger.warning('订阅回调异常: {}', e)

    def _cleanup(self):
        """退出时关闭复用连接"""
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None

    def start(self):
        """启动订阅, 阻塞运行"""
        if not self._targets:
            logger.warning('订阅列表为空, 退出')
            return

        codes = list(self._targets.keys())
        logger.info('===== 订阅启动: {} 只 =====', len(codes))
        for c, n in self._targets.items():
            logger.info('  {} ({})', c, n)

        tq_init()

        # 分批订阅 (最多 100 只)
        for i in range(0, len(codes), 50):
            batch = codes[i:i + 50]
            res = tq.subscribe_hq(stock_list=batch, callback=self.on_data)
            logger.info('订阅批次 {}/{}: {}', i // 50 + 1, (len(codes) - 1) // 50 + 1, res)

        from datetime import time as dtime
        logger.info('进入监控模式 (Ctrl+C 退出)')
        while not self._exit_flag:
            now = datetime.now()
            # 非交易时段 (11:30-13:00 午休 / 15:00后 / 非交易日) 不处理推送
            is_market_open = (
                dtime(9, 15) <= now.time() < dtime(11, 30)
                or dtime(13, 0) <= now.time() < dtime(15, 0)
            )
            # 写心跳
            try:
                hb_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'heartbeats')
                os.makedirs(hb_dir, exist_ok=True)
                with open(os.path.join(hb_dir, 'subscribe.ts'), 'w') as f:
                    f.write(str(time.time()))
            except Exception:
                pass

            if not is_market_open:
                time.sleep(30)  # 非交易时段慢速轮询
                continue
            time.sleep(1)

        try:
            tq.unsubscribe_hq(stock_list=codes)
        except Exception:
            pass
        logger.info('订阅已退出')


# 默认订阅清单 (可被 import 后 add 追加)
_DEFAULT_WATCH = [
    ('002747.SZ', '埃斯顿'),
    ('002008.SZ', '大族激光'),
    ('002580.SZ', '圣阳股份'),
]


if __name__ == '__main__':
    sub = Subscriber()
    for code, name in _DEFAULT_WATCH:
        sub.add(code, name)
    sub.start()
