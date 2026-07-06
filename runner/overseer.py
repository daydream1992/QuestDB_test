"""盘面监工 (Overseer)

脚本路径: K:\QuestDB_test\runner\overseer.py
用途: 非侵入式监控所有 runner 的运行状态 + 数据完整性 + 时间轴检查
执行: python runner/overseer.py
频率: 15 秒/轮，按交易阶段自适应

设计原则:
  - 完全不阻塞 scheduler 的正常运行
  - 通过读 DB 表行数 + 日志 + 进程列表确认状态
  - 每个检查点每天只触发一次 (幂等)
  - 异常分级推送飞书 (INFO / WARN / ERROR)
  - 不管理子进程生命周期 (只告警，由 scheduler 管理)

依赖:
  - lib.qdb (QuestDB 连接 + 查询)
  - lib.market_clock (交易时钟)
  - feishu.push (飞书推送)
  - psutil (进程查找)
  - 不需要 tqcenter / tq_client (不争用 COM 锁)

时间轴检查点:
  1. 09:15 auction_start — 竞价启动确认
  2. 09:25-09:30 daily_init_done — 盘前初始化确认
  3. 09:30-09:35 intraday_started — 实盘切换确认
  4. 11:30-12:55 lunch_mode — 午休确认 + 倒计时
  5. 13:00-13:05 afternoon_resume — 下午盘续确认
  6. 15:00-15:05 close_complete — 收盘确认
  7. 15:05-15:20 daily_close_done — 盘后更新确认
"""

import os
import sys
import time
from datetime import datetime, time as dtime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger
import psutil

from lib.qdb import connect, query_df, cutoff, _ensure_alive
from lib.market_clock import get_phase, is_trading_day
import importlib as _il
_feishu = _il.import_module('feishu')

# ── 日志配置 ──
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_overseer_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# ── 轮询间隔 (秒) 按阶段 ──
_POLL_INTERVALS = {
    'pre_market':   30,   # 盘前
    'auction':      30,   # 竞价
    'morning':     120,   # 上午盘中
    'lunch':       300,   # 午休
    'afternoon':   120,   # 下午盘中
    'pre_close':    30,   # 收盘竞价
    'closed':      600,   # 收盘后
}

# ── 数据检查阈值 ──
_THRESHOLDS = {
    'qd_pricevol':          {'minutes': 5, 'min_rows': 5000, 'consecutive': 2},
    'qd_stock_snapshot':    {'minutes': 2, 'min_rows': 5000, 'consecutive': 2},
    'qd_stock_intraday':    {'minutes': 5, 'min_rows': 100,  'consecutive': 3},
    'qd_indicators':        {'minutes': 30,'min_rows': 5000, 'consecutive': 3},
    'qd_signals':           {'minutes': 30,'min_rows': 50,   'consecutive': 3},
    'qd_decisions':         {'minutes': 30,'min_rows': 1,    'consecutive': 3},
    'qd_sector_flow':       {'minutes': 10,'min_rows': 500,  'consecutive': 2},
    'qd_resonance':         {'minutes': 30,'min_rows': 5000, 'consecutive': 3},
}

# ── 飞书频控 (秒) ──
_COOLDOWN = {'INFO': 0, 'WARN': 300, 'ERROR': 60}

# ── 日志扫描关键词 ──
_LOG_ALERTS = {
    '字段护栏': '策略字段未就位',
    'k1 本轮无新增指标': 'k1 指标窗口不足',
    '推送失败': '飞书链路异常',
    'TQ数据接口已关闭': 'tqcenter COM 异常',
}


# ══════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════

def _table_row_count(con, table, minutes=5):
    """查表最近 N 分钟的行数，失败返回 0"""
    try:
        ts_col = _timestamp_col(table)
        sql = f"SELECT count(*) as c FROM {table} WHERE {ts_col} > '{cutoff(minutes=minutes)}'"
        df = query_df(con, sql)
        return int(df['c'].iloc[0]) if df is not None and not df.empty else 0
    except Exception:
        return 0


def _timestamp_col(table):
    """每个表的时间戳列名 (映射表)"""
    ts = {
        'qd_pricevol': 'snapshot_time',
        'qd_stock_snapshot': 'snapshot_time',
        'qd_stock_intraday': 'snapshot_time',
        'qd_indicators': 'calc_time',
        'qd_signals': 'signal_time',
        'qd_decisions': 'decision_time',
        'qd_sector_flow': 'flow_time',
        'qd_resonance': 'resonance_time',
        'qd_auction_snapshot': 'auction_time',
        'qd_money_flow': 'flow_time',
        'qd_stock_daily': 'date',
    }
    return ts.get(table, 'snapshot_time')


def _find_process(script_name):
    """查找正在运行的子进程，返回 pid 或 None"""
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if any(script_name in str(c) for c in cmdline):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


# ══════════════════════════════════════════════════════════════
# Overseer 主类
# ══════════════════════════════════════════════════════════════

class Overseer:
    """监工守护进程 — 非侵入式监控

    用法:
        overseer = Overseer()
        overseer.run()
    """

    def __init__(self):
        self.con = None
        self.phase = 'pre_market'
        self.last_phase = None
        self._today_str = datetime.now().strftime('%Y-%m-%d')
        self._today_weekday = datetime.now().weekday()
        self._today_is_trading = is_trading_day(datetime.now())
        self._checkpoint_done = {}       # {name|date: iso_timestamp}
        self._last_data_check = {}       # {table: datetime}
        self._consecutive_fails = {}     # {table: fail_count}
        self._last_push = {'WARN': {}, 'ERROR': {}}  # {key: datetime}
        self._summary_hour = -1          # 上次小时汇总的小时数
        self._lunch_countdown_min = -1   # 午休倒计时上次推送分钟

        # 检查点阶段标记：记录每个检查点所属的交易阶段，用阶段切换来判断是否已错过
        self._checkpoint_phases = {
            'auction_start': 'auction',
            'daily_init_done': 'pre_market',
            'intraday_started': 'morning',
            'afternoon_resume': 'afternoon',
            'close_complete': 'closed',
        }
        self._checkpoint_phase_seen = {}  # {name: bool} 标记阶段是否已见过

        # 尝试初始化 DB
        try:
            self.con = connect()
        except Exception as e:
            logger.warning('监工 DB 初始连接失败: {}', e)

    # ─── 日期 & 检查点管理 ─────────────────────────────

    def _current_date(self):
        d = datetime.now().strftime('%Y-%m-%d')
        w = datetime.now().weekday()
        t = is_trading_day(datetime.now())
        if d != self._today_str:
            logger.info('新交易日: {} (检查点缓存重置; 交易日={})', d, t)
            self._today_str = d
            self._today_weekday = w
            self._today_is_trading = t
            self._checkpoint_done.clear()
            self._checkpoint_phase_seen.clear()
            self._consecutive_fails.clear()
            self._summary_hour = -1
            self._lunch_countdown_min = -1
        return self._today_str

    def _checkpoint_once(self, name):
        """幂等: 每个交易日每个检查点只执行一次"""
        date = self._current_date()
        key = f'{name}|{date}'
        if key in self._checkpoint_done:
            return False
        self._checkpoint_done[key] = datetime.now().isoformat()
        logger.info('[监工] 检查点触发: {}', name)
        return True

    def _should_check_data(self, table, interval_seconds=60):
        """控频: 查询频度控制"""
        now = datetime.now()
        last = self._last_data_check.get(table)
        if last is None or (now - last).total_seconds() > interval_seconds:
            self._last_data_check[table] = now
            return True
        return False

    # ─── 飞书推送 ──────────────────────────────────────

    def _notify(self, message, level='INFO'):
        """统一飞书推送，按级别频控"""
        now = datetime.now()
        self._current_date()

        # 频控
        if level in ('WARN', 'ERROR'):
            key = str(hash(message))[-12:]
            last = self._last_push[level].get(key)
            if last and (now - last).total_seconds() < _COOLDOWN[level]:
                return
            self._last_push[level][key] = now

        prefix = {'INFO': '✅', 'WARN': '⚠️', 'ERROR': '🚨'}
        tag = prefix.get(level, 'ℹ️')
        text = f'[监工] {tag} {message}'
        logger.info('{}', text)
        try:
            _feishu.push_text(text)
        except Exception as e:
            logger.warning('监工飞书推送失败: {}', e)

    # ─── DB 保活 ───────────────────────────────────────

    def _ensure_db(self):
        """DB 连接保活"""
        try:
            self.con = _ensure_alive(self.con)
        except Exception as e:
            logger.error('监工 DB 保活失败: {}', e)
            self.con = None

    # ─── 阶段刷新 ──────────────────────────────────────

    def _refresh_phase(self):
        """同步交易时钟"""
        self.last_phase = self.phase
        self.phase = get_phase(datetime.now())
        if self.phase != self.last_phase:
            logger.info('阶段切换: {} → {}', self.last_phase, self.phase)

    # ════════════════════════════════════════════════════
    # 检查点实现
    # ════════════════════════════════════════════════════

    def _check_auction_start(self):
        """检查点 1: 09:15 竞价启动确认"""
        if not self._checkpoint_once('auction_start'):
            return
        pid = _find_process('auction_monitor')
        status = '✅ 运行中' if pid else '❌ 未找到'
        lines = [f'📡 竞价监控已启动 ({datetime.now().strftime("%H:%M")})',
                 f'  ├─ 进程: {status}']
        if pid:
            cnt = _table_row_count(self.con, 'qd_auction_snapshot', minutes=2)
            lines.append(f'  ├─ 数据: qd_auction_snapshot {cnt} 行/2min')
        lines.append(f'  └─ 阶段: pre_open (09:15-09:20 可撤单)')
        self._notify('\n'.join(lines), 'INFO')

    def _check_daily_init_done(self):
        """检查点 2: 09:25-09:30 盘前初始化确认 (只在 daily_init 执行后触发)"""
        now = datetime.now()
        # 限制在 09:25-09:30 之间才触发 (scheduler 在这个窗口执行 daily_init)
        if not (now.hour == 9 and 25 <= now.minute < 30):
            return
        if not self._checkpoint_once('daily_init_done'):
            return
        # 检查 qd_sector_meta 有数据 (c5_mapping 完成)
        meta_cnt = _table_row_count(self.con, 'qd_sector_meta', minutes=60)
        daily_cnt = _table_row_count(self.con, 'qd_stock_daily', minutes=60)
        auction_cnt = _table_row_count(self.con, 'qd_auction_snapshot', minutes=3)

        items = [
            f'  ├─ 板块映射: {meta_cnt} 行',
            f'  ├─ 日级数据: {daily_cnt} 行',
            f'  ├─ 竞价快照: {auction_cnt} 行/3min',
        ]
        status = '✅' if meta_cnt > 0 and daily_cnt > 0 else '⚠️'
        lines = [f'{status} 盘前初始化确认 ({datetime.now().strftime("%H:%M")})']
        lines.extend(items)
        lines.append(f'  └─ 距实盘: 约 {5 - (datetime.now().minute - 25)} 分钟' if datetime.now().minute < 30 else '')
        self._notify('\n'.join(lines), 'INFO')

    def _check_intraday_started(self):
        """检查点 3: 09:30-09:35 实盘切换确认 (只在刚开盘的头 5 分钟触发)"""
        now = datetime.now()
        if not (now.hour == 9 and 30 <= now.minute < 35):
            return
        if not self._checkpoint_once('intraday_started'):
            return

        intra_pid = _find_process('intraday_loop')
        sub_pid = _find_process('subscribe')
        auction_pid = _find_process('auction_monitor')

        lines = [f'🔄 实盘切换 ({datetime.now().strftime("%H:%M")})']
        lines.append(f'  ├─ intraday_loop: {"✅ 运行中" if intra_pid else "❌ 未找到"}')
        lines.append(f'  ├─ subscribe:     {"✅ 运行中" if sub_pid else "❌ 未找到"}')
        lines.append(f'  ├─ auction:       {"✅ 已停止" if not auction_pid else "❌ 仍在跑"}')

        # 确认数据流入
        pv = _table_row_count(self.con, 'qd_pricevol', minutes=1)
        snap = _table_row_count(self.con, 'qd_stock_snapshot', minutes=1)
        lines.append(f'  ├─ 价量: {pv} 行/1min')
        lines.append(f'  ├─ 快照: {snap} 行/1min')

        # 确认策略加载
        from strategy.registry import StrategyRegistry as SR
        active = len(SR.get_all()) if hasattr(SR, 'get_all') else '?'
        lines.append(f'  └─ 策略: {active} 个加载')

        level = 'INFO' if intra_pid and pv > 0 else 'ERROR'
        self._notify('\n'.join(lines), level)

    def _check_lunch_mode(self):
        """检查点 4: 11:30 午休确认 + 倒计时"""
        intra_pid = _find_process('intraday_loop')

        now = datetime.now()
        # 倒计时: 每 5 分钟推一次
        remaining = int((dtime(13, 0) - now.time()).total_seconds() / 60)
        if remaining <= 0:
            return
        # 每 5 分钟或关键倒计时点推送
        countdown_min = (remaining // 5) * 5
        if countdown_min == self._lunch_countdown_min:
            return
        self._lunch_countdown_min = countdown_min

        flag = '' if remaining > 10 else '🔔 '
        lines = [f'☕ 午休 | 距下午开盘 {remaining} 分钟 {flag}']
        lines.append(f'  ├─ intraday_loop: {"💤 休眠中" if intra_pid else "❌ 异常退出"}')
        lines.append(f'  └─ 数据写入: 已暂停 (预期行为)')
        self._notify('\n'.join(lines), 'INFO')

    def _check_afternoon_resume(self):
        """检查点 5: 13:00-13:05 下午盘确认"""
        now = datetime.now()
        if not (now.hour == 13 and now.minute < 5):
            return
        if not self._checkpoint_once('afternoon_resume'):
            return
        intra_pid = _find_process('intraday_loop')
        pv = _table_row_count(self.con, 'qd_pricevol', minutes=2)
        snap = _table_row_count(self.con, 'qd_stock_snapshot', minutes=2)

        items = [
            f'  ├─ intraday_loop: {"✅ 运行中" if intra_pid else "❌ 未找到"}',
            f'  ├─ 价量: {pv} 行/2min',
            f'  ├─ 快照: {snap} 行/2min',
        ]
        level = 'INFO' if intra_pid and pv > 0 else 'WARN'
        lines = [f'🔁 下午盘恢复 ({datetime.now().strftime("%H:%M")})']
        lines.extend(items)
        self._notify('\n'.join(lines), level)

    def _check_close_complete(self):
        """检查点 6: 15:00-15:05 收盘确认"""
        now = datetime.now()
        if not (now.hour == 15 and now.minute < 5):
            return
        if not self._checkpoint_once('close_complete'):
            return
        intra_pid = _find_process('intraday_loop')
        sub_pid = _find_process('subscribe')
        lines = [f'🔚 收盘确认 ({datetime.now().strftime("%H:%M")})',
                 f'  ├─ intraday_loop: {"✅ 已退出" if not intra_pid else "⚠️ 仍在跑"}',
                 f'  ├─ subscribe:     {"✅ 已退出" if not sub_pid else "⚠️ 仍在跑"}',
                 f'  └─ 盘后任务: daily_close 即将执行']
        self._notify('\n'.join(lines), 'INFO')

    # ════════════════════════════════════════════════════
    # 持续检查
    # ════════════════════════════════════════════════════

    def _check_process_health(self):
        """盘中/竞价进程存活检查"""
        if self.phase in ('morning', 'afternoon'):
            for name in ('intraday_loop', 'subscribe'):
                if not _find_process(name):
                    self._notify(f'盘中进程缺失: {name}', 'ERROR')
        elif self.phase == 'auction':
            if not _find_process('auction_monitor'):
                self._notify(f'竞价进程缺失: auction_monitor', 'ERROR')

    def _check_data_integrity(self):
        """数据写入完整性检查 (按表控频)"""
        if self.phase not in ('morning', 'afternoon'):
            return

        for table, cfg in _THRESHOLDS.items():
            if not self._should_check_data(table, interval_seconds=cfg['minutes'] * 10):
                continue
            cnt = _table_row_count(self.con, table, minutes=cfg['minutes'])
            key = f'data:{table}'
            if cnt < cfg['min_rows']:
                fails = self._consecutive_fails.get(key, 0) + 1
                self._consecutive_fails[key] = fails
                if fails >= cfg['consecutive']:
                    self._notify(f'{table} 连续 {fails} 次写入不足: {cnt}/{cfg["min_rows"]} 行/{cfg["minutes"]}min', 'WARN')
                    self._consecutive_fails[key] = 0  # 重置，避免重复告警
            else:
                self._consecutive_fails[key] = 0

    def _scan_logs(self):
        """日志异常扫描 — 每 5 分钟"""
        if not self._should_check_data('log_scan', interval_seconds=300):
            return

        now = datetime.now()
        date_str = now.strftime('%Y%m%d')
        log_files = [
            ('intraday_loop', f'logs/runner_intraday_loop_{date_str}.log'),
            ('scheduler', f'logs/runner_scheduler_{date_str}.log'),
        ]

        for tag, rel_path in log_files:
            abs_path = os.path.join(_PROJ_ROOT, rel_path)
            if not os.path.exists(abs_path):
                continue
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()[-300:]  # 最后 300 行
                content = '\n'.join(lines)

                err_count = content.count('ERROR')
                warn_count = content.count('WARNING')

                # ERROR 激增告警
                if err_count > 5:
                    last_errs = [l.strip() for l in lines if 'ERROR' in l][-3:]
                    self._notify(f'{tag} 日志 {err_count} ERROR, 最后: {last_errs[0] if last_errs else ""}', 'WARN')

                # 关键词检测
                for keyword, desc in _LOG_ALERTS.items():
                    if keyword in content:
                        self._notify(f'{tag} 日志: {desc}', 'WARN')
            except Exception as e:
                logger.warning('日志扫描失败 {}: {}', tag, e)

    def _hourly_summary(self):
        """每小时汇总推送"""
        hour = datetime.now().hour
        if hour < 10 or hour >= 15:
            return
        if hour == self._summary_hour:
            return
        self._summary_hour = hour

        # 采集统计
        pv = _table_row_count(self.con, 'qd_pricevol', minutes=5)
        snap = _table_row_count(self.con, 'qd_stock_snapshot', minutes=5)
        intra = _table_row_count(self.con, 'qd_stock_intraday', minutes=5)
        sig = _table_row_count(self.con, 'qd_signals', minutes=60)
        dec = _table_row_count(self.con, 'qd_decisions', minutes=60)

        lines = [f'📊 [{hour}:00] 盘中快报']
        lines.append(f'  ├─ 采集: 价量 {pv} | 快照 {snap} | 主力 {intra}')
        lines.append(f'  ├─ 信号: {sig} 条/60min | 决策: {dec} 条/60min')
        lines.append(f'  └─ 错误: {self._consecutive_fails}')
        self._notify('\n'.join(lines), 'INFO')

    # ════════════════════════════════════════════════════
    # 主循环
    # ════════════════════════════════════════════════════

    def _get_interval(self):
        return _POLL_INTERVALS.get(self.phase, 30)

    def run(self):
        """监工主循环"""
        logger.info('===== 监工启动 {} =====', datetime.now())

        # 启动检查点
        self._notify(f'监工已启动 ({datetime.now().strftime("%H:%M")})', 'INFO')

        while True:
            try:
                t0 = time.time()
                self._refresh_phase()
                self._current_date()
                self._ensure_db()

                if not self._today_is_trading:
                    logger.debug('非交易日, 跳过检查')
                    time.sleep(600)
                    continue

                if self.con is None:
                    logger.warning('DB 不可用, 跳过检查')
                    time.sleep(30)
                    continue

                # ── 时间轴检查点 (基于阶段切换, 而非硬编码时间) ──
                # 进入新阶段时触发对应的检查点 (每个阶段只触发一次)
                cp_key = self.phase
                if cp_key not in self._checkpoint_phase_seen:
                    self._checkpoint_phase_seen[cp_key] = True
                    # pre_market 阶段可能出现两次 (08:00-09:14 + 09:25-09:30)
                    # 只在 09:25-09:30 窗口才触发 daily_init 检查
                    if self.phase == 'pre_market':
                        now = datetime.now()
                        if now.hour == 9 and 25 <= now.minute < 30:
                            self._check_daily_init_done()
                    elif self.phase == 'auction':
                        self._check_auction_start()
                    elif self.phase == 'morning':
                        self._check_intraday_started()
                    elif self.phase == 'afternoon':
                        self._check_afternoon_resume()
                    elif self.phase == 'closed':
                        self._check_close_complete()

                # ── 持续检查 (每轮按阶段执行) ──
                if self.phase in ('morning', 'afternoon'):
                    self._check_process_health()
                    self._check_data_integrity()
                    self._scan_logs()
                    self._hourly_summary()
                elif self.phase == 'lunch':
                    self._check_lunch_mode()
                elif self.phase == 'pre_close':
                    pass  # 收盘竞价由 intraday_loop 自行处理

                # 睡眠
                elapsed = time.time() - t0
                interval = self._get_interval()
                time.sleep(max(1, interval - elapsed))

            except KeyboardInterrupt:
                logger.info('Ctrl+C 退出监工')
                break
            except Exception as e:
                logger.error('监工主循环异常: {}', e)
                try:
                    self._notify(f'监工主循环异常: {e}', 'ERROR')
                except Exception:
                    pass
                time.sleep(30)

        logger.info('===== 监工退出 =====')


def main():
    __doc__ = __doc__ or ''
    overseer = Overseer()
    try:
        overseer.run()
    except KeyboardInterrupt:
        logger.info('Ctrl+C 退出监工')
    finally:
        _feishu.push_text('[监工] 监工已退出')
        logger.info('===== 监工退出 =====')


if __name__ == '__main__':
    main()
