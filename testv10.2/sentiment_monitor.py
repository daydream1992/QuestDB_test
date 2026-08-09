"""testv10.2 大盘情绪监控 (sentiment_monitor) — 原始数据 + 综合情绪分 → 飞书多维表

脚本路径: K:/QuestDB_test/testv10.2/sentiment_monitor.py
设计: 《盘中大盘情绪量化方案》— 8 维度原始数据 + 加权 0-100 综合分, 每 1min 一行写飞书。
接口: get_market_snapshot / get_pricevol / get_more_info (实时三件套)。

数据源 (探针 2026-08-09 全绿):
  广度+极端  880001.SH 单点 (UpHome/DownHome 涨跌家数 + Outside/Inside 涨跌停家数)
  成交额     880001.SH snapshot Amount (万元) + 主力净流 more_info Zjl_HB (万元)
  亏钱       df (scan_universe 全A pct 分布) — 复用 funnel 的 df, 不重复拉
  连板/封板/炸板  df.pct≥9.5 候选 → get_more_info(EverZTCount/FCAmo/FCb/ZTPrice) + snapshot(Max)
  持续性     880863.SH 昨日涨停指数涨幅
  风向       4 宽基加权涨幅 (沪深300·40/创业·30/科创·20/深成·10)
  板块       复用 meso rows (Top3 涨停家数)

架构:
  - 自包含取数, 但复用 funnel 传入的 df (省一次 scan_universe); df=None 时自取。
  - 字段配置化: SENTIMENT_FIELDS 一处定义, 控制台打印 + 飞书表 schema 同源; 增删字段改这里。
依赖: lib.tq_client, ticker, feishu.bitable_writer (写表), settings
红线: dry_run 默认 True; 1min 门控; 写表失败不崩 funnel (log)。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402  (计算层: 零 tq 调用, 只消费 bundle)

# ── 8 维度权重 (和=1.0) + 归一化区间 ──
WEIGHTS = {
    'breadth': 0.15, 'extreme': 0.15, 'loss': 0.15, 'ladder': 0.15,
    'seal': 0.10, 'continuity': 0.10, 'wind': 0.10, 'sector': 0.10,
}
NORM = {
    'udr': (0.5, 3.0), 'zt': (10.0, 80.0), 'dt': (5.0, 40.0),
    'loss_ratio': (0.5, 2.0), 'max_lb': (1.0, 5.0), 'fcb': (0.05, 0.8),
    'cont_chg': (-2.0, 3.0), 'wind_chg': (-1.5, 1.5), 'sector_zt': (0.0, 30.0),
}

# ════════════════════════════════════════════════════════════════════
# 字段定义 (一处配置; 控制台打印 + 飞书表 schema 共用)
# 增删字段: 改本表 + 确保 compute() 返回的 result 含对应 key (或 _build_record 派生)
#   key='xxx'  → 取 result['xxx']
#   key='_xx'  → _build_record 派生 (单位换算/拆分 idx_chgs/hot3 等)
# ════════════════════════════════════════════════════════════════════
_LABEL_OPTS = [{'name': n, 'color': c} for n, c in
               [('冰点', 1), ('中性', 3), ('偏暖', 0), ('高潮', 2)]]
SENTIMENT_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '综合情绪分', 'type': 2, 'key': 'score'},
    {'field_name': '档位', 'type': 3, 'key': 'label', 'options': _LABEL_OPTS},
    {'field_name': '趋势', 'type': 1, 'key': 'trend'},
    {'field_name': '上涨家数', 'type': 2, 'key': 'up_home'},
    {'field_name': '下跌家数', 'type': 2, 'key': 'down_home'},
    {'field_name': '涨停数', 'type': 2, 'key': 'zt_cnt'},
    {'field_name': '跌停数', 'type': 2, 'key': 'dt_cnt'},
    {'field_name': '炸板数', 'type': 2, 'key': 'blasted'},
    {'field_name': '封板率%', 'type': 2, 'key': 'fbl'},
    {'field_name': '封成比', 'type': 2, 'key': 'avg_fcb'},
    {'field_name': '封单均额亿', 'type': 2, 'key': '_fcamo_yi'},
    {'field_name': '主力流入亿', 'type': 2, 'key': '_main_yi'},
    {'field_name': '今日成交万亿', 'type': 2, 'key': '_amt_yi'},
    {'field_name': '昨日成交万亿', 'type': 2, 'key': '_amt_y_prev'},
    {'field_name': '最高连板', 'type': 2, 'key': 'max_lb'},
    {'field_name': '≥2连板数', 'type': 2, 'key': 'lb_ge2'},
    {'field_name': '昨连板表现%', 'type': 2, 'key': 'cont_chg'},
    {'field_name': '昨涨停续封', 'type': 2, 'key': 'cont_zt'},
    {'field_name': '沪深300%', 'type': 2, 'key': '_hs300'},
    {'field_name': '创业板%', 'type': 2, 'key': '_cyb'},
    {'field_name': '科创50%', 'type': 2, 'key': '_kc50'},
    {'field_name': '深证%', 'type': 2, 'key': '_sz'},
    {'field_name': '热门Top1', 'type': 1, 'key': '_hot1'},
    {'field_name': '热门Top2', 'type': 1, 'key': '_hot2'},
    {'field_name': '热门Top3', 'type': 1, 'key': '_hot3'},
    {'field_name': '涨超5%数', 'type': 2, 'key': 'up5'},
    {'field_name': '跌超5%数', 'type': 2, 'key': 'down5'},
    {'field_name': '亏钱比', 'type': 2, 'key': 'loss_ratio'},
    {'field_name': '数据质量', 'type': 1, 'key': 'data_quality'},
    {'field_name': '结论', 'type': 1, 'key': 'conclusion'},
]


def _f(v, default=0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r  # NaN 兜底
    except (TypeError, ValueError):
        return default


def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _label(score: float) -> str:
    if score >= 80:
        return '高潮'
    if score >= 60:
        return '偏暖'
    if score >= 40:
        return '中性'
    return '冰点'


def _bitable_fields() -> list:
    """SENTIMENT_FIELDS → bitable _create_field 入参 (剥 key, 留 field_name/type/options)。"""
    out = []
    for fd in SENTIMENT_FIELDS:
        d = {'field_name': fd['field_name'], 'type': fd['type']}
        if 'options' in fd:
            d['options'] = fd['options']
        out.append(d)
    return out


# 时段聚合表字段 (v10.2情绪时段; 每段 1 行)
def _slot_fields() -> list:
    return [
        {'field_name': '时间', 'type': 5, 'key': '_ts'},
        {'field_name': '时段', 'type': 1, 'key': '时段'},
        {'field_name': '情绪分开', 'type': 2}, {'field_name': '情绪分收', 'type': 2},
        {'field_name': '情绪分高', 'type': 2}, {'field_name': '情绪分低', 'type': 2},
        {'field_name': '涨停峰值', 'type': 2}, {'field_name': '趋势', 'type': 1},
        {'field_name': '封板率', 'type': 2}, {'field_name': '炸板', 'type': 2},
    ]


class SentimentMonitor:
    """大盘情绪监控: 每 N 秒取数→算分→写飞书一行。底座零侵入 (仅接收 funnel 的 df/rows)。"""

    def __init__(self, ms=None, pub=None, dry_run: bool | None = None):
        self.ms = ms               # mapping_store (个股名/板块成分, L3 定位用)
        self.pub = pub             # publisher (联动警报卡走 webhook; None 则不发警报)
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.last_push_ts: datetime | None = None
        self.prev_score: float | None = None    # 趋势 ↑↓→
        self._amount_cache_path = os.path.join(cfg.LOG_DIR, 'sentiment_amount.json')
        self._amount_cache: dict | None = None
        self._bw = None   # 懒加载 feishu.bitable_writer
        self.last_result: dict | None = None   # 最近一轮 result (供 alert_engine 读)
        # 时段聚合: 当前时段名 + 累积 result (段末写 1 行聚合表)
        self._slot_name: str | None = None
        self._slot_results: list[dict] = []

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    # ── 成交额跨日缓存 (今日实时存, 昨日读) ──
    def _load_amount_cache(self) -> dict:
        if self._amount_cache is not None:
            return self._amount_cache
        try:
            with open(self._amount_cache_path, encoding='utf-8') as f:
                self._amount_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._amount_cache = {}
        return self._amount_cache

    def _yesterday_amount(self) -> float:
        cache = self._load_amount_cache()
        yd = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        return float(cache.get(yd, -1))

    def _save_today_amount(self, amount_wan: float) -> None:
        cache = self._load_amount_cache()
        today = datetime.now().strftime('%Y-%m-%d')
        if cache.get(today, -1) != amount_wan:
            cache[today] = amount_wan
            try:
                os.makedirs(cfg.LOG_DIR, exist_ok=True)
                with open(self._amount_cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
            except OSError as e:  # noqa: BLE001
                logger.debug('成交额缓存写失败: {}', e)

    # ============ 门控入口 (radar_main 每轮调; 吃 data_provider bundle) ============
    def maybe_push(self, bundle: dict, rows: list[dict], now: datetime) -> bool:
        if not cfg.SENTIMENT_ENABLE:
            return False
        if 'sentiment_raw' not in bundle:   # 该 stage 无情绪采集 (如竞价/开盘), 跳过
            return False
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.SENTIMENT_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        try:
            result = self.compute(bundle, rows)
        except Exception:  # noqa: BLE001  红线: 情绪监控失败不崩 funnel
            logger.exception('大盘情绪 compute 失败, 跳过本轮')
            return False
        self._write(result, now)
        self.last_result = result   # 供 alert_engine 读 (预警归 alert_engine)
        return True

    def force_push(self, bundle: dict, rows: list[dict], now: datetime) -> bool:
        """绕门控写一行 (收盘定格用; 不触发预警)。"""
        if 'sentiment_raw' not in bundle:
            return False
        try:
            result = self.compute(bundle, rows)
        except Exception:  # noqa: BLE001
            logger.exception('sentiment force_push 失败')
            return False
        self._write(result, now)
        self.last_result = result   # close 段 alert_engine 读到最新值
        # close 段是当日最后时段, 显式 flush 尾盘聚合行 (段末无切换触发)
        self.flush_slot(now)
        return True

    def flush_slot(self, now: datetime) -> None:
        """显式 flush 当前时段聚合 (close 段调用, 补尾盘最后一行)。"""
        if self._slot_name and self._slot_results:
            self._write_slot_row(self._slot_name, self._slot_results, now)
        self._slot_name = None
        self._slot_results = []

    # ============ 算分 (纯计算; raw 来自采集层 data_provider bundle) ============
    def compute(self, bundle: dict, rows: list[dict]) -> dict:
        return self._calc(bundle['sentiment_raw'], rows)

    def _calc(self, raw: dict, rows: list[dict]) -> dict:
        mkt = raw['market']
        a = mkt.get(cfg.MARKET_ALL_INDEX, {})
        uph = _f(a.get('UpHome')); dnh = _f(a.get('DownHome'))
        zt = _f(a.get('Outside')); dt = _f(a.get('Inside'))
        if zt > cfg.SENTIMENT_SANITY_ZT_MAX:
            logger.warning('880001 Outside={} 超 sanity, 疑非全A指数, 置 0', zt)
            zt = 0.0
        udr = (uph / dnh) if dnh > 0 else (1.0 if uph > 0 else 0.0)
        d1 = 100 * _norm(udr, *NORM['udr'])
        d2 = 100 * (0.6 * _norm(zt, *NORM['zt']) + 0.4 * (1.0 - _norm(dt, *NORM['dt'])))

        br = raw['breadth']
        up5, down5 = br['up5'], br['down5']
        # 双零 (无涨跌超5%) = 中性 1.0 → 50 分, 不虚抬满分
        loss_ratio = ((down5 / up5) if up5 > 0 else
                      (2.0 if down5 > 0 else 1.0))
        d3 = 100 * (1.0 - _norm(loss_ratio, *NORM['loss_ratio']))

        cand = raw['candidates']
        sealed = [c for c in cand if c['FCAmo'] > 0]
        blasted = sum(1 for c in cand
                      if c['FCAmo'] <= 0 and c['ZTPrice'] > 0 and c['Max'] >= c['ZTPrice'] - 0.001)
        fbl = (len(sealed) / (len(sealed) + blasted) * 100) if (len(sealed) + blasted) > 0 else 0.0
        max_lb = max((c['EverZTCount'] for c in cand if c['EverZTCount'] > 0), default=0.0)
        lb_ge2 = sum(1 for c in cand if c['EverZTCount'] >= 2)
        d4 = 100 * _norm(max_lb, *NORM['max_lb'])
        avg_fcb = (sum(c['FCb'] for c in sealed) / len(sealed)) if sealed else 0.0
        avg_fcamo = (sum(c['FCAmo'] for c in sealed) / len(sealed)) if sealed else 0.0
        d5 = 100 * _norm(avg_fcb, *NORM['fcb']) if sealed else 0.0

        y = mkt.get(cfg.YESTERDAY_ZT_INDEX, {})
        y_now = _f(y.get('Now')); y_lc = _f(y.get('LastClose'))
        cont_chg = ((y_now - y_lc) / y_lc * 100) if y_lc > 0 else 0.0
        cont_zt = _f(y.get('Outside'))
        d6 = 100 * _norm(cont_chg, *NORM['cont_chg'])

        wind_chg = 0.0
        idx_chgs: dict[str, float] = {}
        for code, (name, w) in cfg.BROAD_INDICES.items():
            sn = mkt.get(code, {})
            n = _f(sn.get('Now')); lc = _f(sn.get('LastClose'))
            chg = ((n - lc) / lc * 100) if lc > 0 else 0.0
            idx_chgs[name] = chg
            wind_chg += w * chg
        d7 = 100 * _norm(wind_chg, *NORM['wind_chg'])

        hot = sorted(rows, key=lambda r: r.get('ZTGPNum', 0), reverse=True)[:5] if rows else []
        sector_zt = sum(r.get('ZTGPNum', 0) for r in hot)
        d8 = 100 * _norm(sector_zt, *NORM['sector_zt'])
        hot3 = [(r['name'], int(r.get('ZTGPNum', 0))) for r in hot[:3]]

        amount_today = raw['amount_today']
        amount_yesterday = self._yesterday_amount()
        self._save_today_amount(amount_today)
        main_net = raw['main_net']

        subs = {'breadth': d1, 'extreme': d2, 'loss': d3, 'ladder': d4,
                'seal': d5, 'continuity': d6, 'wind': d7, 'sector': d8}
        score = sum(WEIGHTS[k] * subs[k] for k in WEIGHTS)
        if self.prev_score is not None:
            delta = score - self.prev_score
            trend = '↑' if delta > 1 else ('↓' if delta < -1 else '→')
        else:
            trend = '·'
        self.prev_score = score

        dq_parts = []
        if raw['cand_drill_degraded']:
            dq_parts.append('连板/封板部分降级(预算截断)')
        if not sealed:
            dq_parts.append('无实时封板(盘后/弱市)')
        data_quality = '实时8维度' + ('·' + ';'.join(dq_parts) if dq_parts else '')

        return {
            'score': round(score, 1), 'label': _label(score), 'trend': trend,
            'subs': {k: round(v, 1) for k, v in subs.items()},
            'up_home': int(uph), 'down_home': int(dnh), 'udr': round(udr, 2),
            'zt_cnt': int(zt), 'dt_cnt': int(dt), 'blasted': blasted, 'fbl': round(fbl, 1),
            'up5': up5, 'down5': down5, 'loss_ratio': round(loss_ratio, 2),
            'max_lb': int(max_lb), 'lb_ge2': lb_ge2,
            'avg_fcb': round(avg_fcb, 3), 'avg_fcamo': round(avg_fcamo, 0), 'sealed_n': len(sealed),
            'cont_chg': round(cont_chg, 2), 'cont_zt': int(cont_zt),
            'wind_chg': round(wind_chg, 2), 'idx_chgs': {k: round(v, 2) for k, v in idx_chgs.items()},
            'hot3': hot3, 'sector_zt': int(sector_zt),
            'amount_today': amount_today, 'amount_yesterday': amount_yesterday, 'main_net': main_net,
            'data_quality': data_quality,
            'conclusion': self._conclude(udr, zt, blasted, loss_ratio, max_lb, avg_fcb,
                                         len(sealed), cont_chg),
            'duration': raw['fetch_duration'],
        }

    @staticmethod
    def _conclude(udr, zt, blasted, loss_ratio, max_lb, avg_fcb, sealed_n, cont_chg) -> str:
        parts = []
        if udr > 1.5 and zt > 50:
            parts.append('广度极端共振, 情绪偏强')
        elif zt > 50 and udr < 1.2:
            parts.append(f'涨幅集中(涨停{int(zt)}家但涨跌比仅{udr:.2f}), 非普涨')
        elif udr < 0.7:
            parts.append('广度偏弱, 跌多涨少')
        elif udr > 2:
            parts.append('广度强, 普涨')
        if loss_ratio > 1.5:
            parts.append(f'亏钱效应扩散(亏钱比{loss_ratio:.2f})')
        elif loss_ratio < 0.2:
            parts.append('赚钱效应强')
        if zt > 0 and blasted > zt * 0.5:
            parts.append(f'炸板率高(炸{blasted}>封{int(zt)}的50%), 追高风险')
        elif blasted > 20:
            parts.append(f'炸板{blasted}只偏多')
        if max_lb >= 5:
            parts.append(f'连板高度{int(max_lb)}活跃, 情绪高潮')
        if sealed_n > 0 and avg_fcb < 0.3:
            parts.append('封板质量差(封成比<0.3)')
        if cont_chg < -1:
            parts.append('昨日涨停股大面积回落, 持续性差')
        elif cont_chg > 2:
            parts.append('昨日涨停股继续走强, 持续性好')
        return '; '.join(parts) if parts else '情绪中性'

    # 预警逻辑 (_check_alert / _detect_dive / _locate) 已移至 alert_engine — sentiment 只管算分+写表

    # ============ 输出 ============
    def _write(self, result: dict, now: datetime) -> None:
        self._print(result, now)
        if not self.dry_run:
            try:
                self._write_bitable(result, now)   # 240 行 1min 表 (保留, 供飞书分析)
            except Exception:  # noqa: BLE001  写表失败不崩
                logger.exception('飞书写表失败')
        self._accum_slot(result, now)              # 时段聚合 (段末写 1 行聚合表)

    def _accum_slot(self, result: dict, now: datetime) -> None:
        """时段聚合: 累积当前时段 result, 段边界时写聚合行到独立表。

        时段定义 settings.SENTIMENT_SLOTS (早/午/后/尾)。保留 240 行 1min 表,
        另加 4 行/天时段聚合供快速决策。"""
        slot = None
        t = now.time()
        for name, lo, hi in cfg.SENTIMENT_SLOTS:
            if lo <= t < hi:
                slot = name
                break
        if slot != self._slot_name:
            # 时段切换: 若前一时段有累积, 写聚合行
            if self._slot_name and self._slot_results:
                self._write_slot_row(self._slot_name, self._slot_results, now)
            self._slot_name = slot
            self._slot_results = []
        if slot:
            self._slot_results.append(result)

    def _write_slot_row(self, label: str, results: list[dict], now: datetime) -> None:
        """写时段聚合行: 该段情绪分开/高/低/收 + 涨停峰值 + 趋势。"""
        if not results:
            return
        scores = [r['score'] for r in results]
        zt_peak = max(r['zt_cnt'] for r in results)
        first, last = results[0], results[-1]
        trend = '↑' if last['score'] > first['score'] + 1 else (
            '↓' if last['score'] < first['score'] - 1 else '→')
        logger.info('📊 情绪时段 | {} | 分 {}→{} 最高{} 最低{} | 涨停峰值{} | 趋势{}',
                    label, first['score'], last['score'], max(scores), min(scores),
                    zt_peak, trend)
        if self.dry_run or not cfg.SENTIMENT_BITABLE_APP_TOKEN:
            return
        try:
            table_id = self.bw.auto_named_table(
                cfg.SENTIMENT_BITABLE_APP_TOKEN, f'v10.2情绪时段 {now.strftime("%Y-%m-%d")}',
                _slot_fields())
            if not table_id:
                return
            record = {'时间': int(now.timestamp() * 1000), '时段': label,
                      '情绪分开': round(first['score'], 1), '情绪分收': round(last['score'], 1),
                      '情绪分高': round(max(scores), 1), '情绪分低': round(min(scores), 1),
                      '涨停峰值': zt_peak, '趋势': trend,
                      '封板率': round(last['fbl'], 1), '炸板': last['blasted']}
            self.bw.append_records(cfg.SENTIMENT_BITABLE_APP_TOKEN, table_id, [record])
        except Exception:  # noqa: BLE001
            logger.exception('时段聚合写表失败')

    def _print(self, result: dict, now: datetime) -> None:
        """控制台/日志打印 (像看盘: 原始数据 + 结论)。"""
        r = result
        idx = r['idx_chgs']
        amt_y = r['amount_yesterday']
        amt_y_s = f'{amt_y / 1e8:.2f}万亿' if amt_y > 0 else '-'
        hot3_s = '  '.join(f'{n} {z}' for n, z in r['hot3']) or '-'
        logger.info(
            '📊 大盘情绪 | {} {}{}\n'
            '上涨 {:<6}  下跌 {:<6}\n'
            '涨停 {:<4}  跌停 {:<4}  炸板 {}\n'
            '今日成交 {:.2f}万亿  (昨日 {})\n'
            '主力流入 {:+.0f}亿\n'
            '今日封板率 {:.1f}%  (封{} 炸{})  封成比 {:.2f}  封单均 {:.2f}亿\n'
            '昨连板表现 {:+.2f}% (续封{})  最高连板 {}板 (≥2板{}只)\n'
            '沪深300 {:+.2f}%  创业 {:+.2f}%  科创 {:+.2f}%  深成 {:+.2f}%\n'
            '{}\n'
            '结论: {}  (综合 {:.1f} | {:.1f}s)',
            now.strftime('%H:%M'), r['label'], r['trend'],
            r['up_home'], r['down_home'],
            r['zt_cnt'], r['dt_cnt'], r['blasted'],
            r['amount_today'] / 1e8, amt_y_s,
            r['main_net'] / 1e4,
            r['fbl'], r['zt_cnt'], r['blasted'], r['avg_fcb'], r['avg_fcamo'] / 1e4,
            r['cont_chg'], r['cont_zt'], r['max_lb'], r['lb_ge2'],
            idx.get('沪深300', 0), idx.get('创业板', 0), idx.get('科创50', 0), idx.get('深证', 0),
            hot3_s,
            r['conclusion'], r['score'], r['duration'])

    def _build_record(self, result: dict, now: datetime) -> dict:
        """result → 飞书表记录 (字段名→值)。派生 key 以 _ 开头 (单位换算/拆分)。"""
        idx = result['idx_chgs']
        hot = result['hot3']
        amt_y = result['amount_yesterday']
        flat = {
            '_ts': int(now.timestamp() * 1000),
            '_fcamo_yi': round(result['avg_fcamo'] / 1e4, 2),
            '_main_yi': round(result['main_net'] / 1e4, 0),
            '_amt_yi': round(result['amount_today'] / 1e8, 2),
            '_amt_y_prev': round(amt_y / 1e8, 2) if amt_y > 0 else None,
            '_hs300': idx.get('沪深300', 0), '_cyb': idx.get('创业板', 0),
            '_kc50': idx.get('科创50', 0), '_sz': idx.get('深证', 0),
            '_hot1': hot[0][0] if len(hot) > 0 else '',
            '_hot2': hot[1][0] if len(hot) > 1 else '',
            '_hot3': hot[2][0] if len(hot) > 2 else '',
        }
        flat.update(result)
        record: dict = {}
        for fd in SENTIMENT_FIELDS:
            v = flat.get(fd['key'])
            if v is None or v == '':
                continue   # 空值跳过 (单选字段空值会 option 不匹配)
            record[fd['field_name']] = v
        return record

    def _write_bitable(self, result: dict, now: datetime) -> None:
        """写一行到 v10.2 专属飞书表 (按日建子表)。app_token 首次自动建并固化。"""
        app_token = cfg.SENTIMENT_BITABLE_APP_TOKEN
        if not app_token:
            created = self.bw.create_bitable('v10.2大盘情绪监控') or {}
            app_token = created.get('app_token', '')
            if not app_token:
                logger.error('create_bitable 失败, 跳过写表')
                return
            logger.warning('⚠️ 新建飞书表成功, app_token={}, 请回填 settings.SENTIMENT_BITABLE_APP_TOKEN',
                           app_token)
        table_id = self.bw.auto_named_table(app_token, f'v10.2情绪 {now.strftime("%Y-%m-%d")}',
                                           _bitable_fields())
        if not table_id:
            logger.error('auto_named_table 失败, 跳过写表')
            return
        record = self._build_record(result, now)
        ok = self.bw.append_records(app_token, table_id, [record])
        logger.info('飞书写表 {} ({}字段, 综合分{})', '✅' if ok else '❌', len(record), result['score'])


if __name__ == '__main__':
    # 自检: python testv10.2/sentiment_monitor.py        (打印一轮)
    #       python testv10.2/sentiment_monitor.py --write (真写飞书1行)
    import sys  # noqa: E402
    from lib.tq_client import init, close  # noqa: E402
    import mapping_store as ms_mod  # noqa: E402
    from meso_radar import MesoRadar  # noqa: E402

    do_write = '--write' in sys.argv
    init()
    try:
        ms = ms_mod.MappingStore(cfg.MAPPING_PARQUET)
        radar = MesoRadar(ms, cfg.MONITOR_LEVELS)
        rows = radar.scan()
        mon = SentimentMonitor(ms, None, dry_run=not do_write)   # pub=None (预警归 alert_engine)
        import data_provider  # noqa: E402
        bundle = data_provider.fetch_bundle('intraday', None)
        result = mon.compute(bundle, rows)
        mon._write(result, datetime.now())
    finally:
        close()
