"""testv10.2 尾盘监控 (tail) — 14:00-15:00 尾盘炸板风险

脚本路径: K:/QuestDB_test/testv10.2/tail_monitor.py
传导链风险收尾: 跨轮检测候选 曾封(FCAmo>0)→现开(≤0) = 尾盘炸板 (高标炸板预警)。
消费 bundle sentiment_raw candidates 的 FCAmo (复用, 不重复采集)。
尾盘拉升(Zangsu>3 全A扫描)留后续 — 全A snapshot 5500 次太贵, 先不做。
计算层: 零 tq (raw 来自 bundle)。红线: dry_run 默认; 门控; 写表失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

TAIL_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '封板候选数', 'type': 2, 'key': 'sealed_n'},
    {'field_name': '尾盘炸板数', 'type': 2, 'key': 'blast_n'},
    {'field_name': '炸板股', 'type': 1, 'key': 'blast_s'},
]


def _bitable_fields() -> list:
    return [{'field_name': fd['field_name'], 'type': fd['type']} for fd in TAIL_FIELDS]


class TailMonitor:
    """尾盘炸板监控: 候选 FCAmo 跨轮, 曾封现开 = 炸板。"""

    def __init__(self, ms=None, dry_run: bool | None = None):
        self.ms = ms
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.prev_fcamo: dict[str, float] = {}   # 跨轮 code -> 上轮 FCAmo
        self.last_push_ts: datetime | None = None
        self._bw = None
        self.last_result: dict = {}   # 供 alert_engine 读

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    def maybe_push(self, bundle: dict, now: datetime) -> bool:
        if 'sentiment_raw' not in bundle:
            return False
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.TAIL_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        cands = bundle['sentiment_raw']['candidates']
        sealed = [c for c in cands if c['FCAmo'] > 0]
        # 炸板: 上轮 FCAmo>0, 本轮 ≤0 (曾封现开)
        blast: list[tuple] = []   # (code, prev_fcamo, cur_fcamo)
        cur_codes: set[str] = set()
        for c in cands:
            cur_codes.add(c['code'])
            prev = self.prev_fcamo.get(c['code'])
            if prev is not None and prev > 0 and c['FCAmo'] <= 0:
                blast.append((c['code'], prev, c['FCAmo']))
            elif prev is None and c['FCAmo'] > 0:
                # 基线: 首轮/tail 首见已封的股, 设基线防首轮误判 (不记炸板)
                pass
            self.prev_fcamo[c['code']] = c['FCAmo']
        # 清理: 不在本轮候选的 code 移除 prev (防跌出后回候选误判为炸板)
        for c in [c for c in self.prev_fcamo if c not in cur_codes]:
            self.prev_fcamo.pop(c, None)
        self._write(sealed, blast, now)
        return True

    def _write(self, sealed, blast, now):
        blast_names = []
        for code, prev, cur in blast[:10]:
            name = self.ms.stock_name(code) if self.ms else code
            blast_names.append(f'{name}({prev:.0f}→{cur:.0f})')
        logger.info('📉 尾盘 | {} | 封板候选{} 炸板{} | {}',
                    now.strftime('%H:%M'), len(sealed), len(blast),
                    ' / '.join(blast_names) or '无炸板')
        self.last_result = {'sealed_n': len(sealed), 'blast_n': len(blast),
                            'blast_s': ' / '.join(blast_names) or '-'}
        if self.dry_run or not cfg.SENTIMENT_BITABLE_APP_TOKEN:
            return
        try:
            table_id = self.bw.auto_named_table(cfg.SENTIMENT_BITABLE_APP_TOKEN,
                                                f'v10.2尾盘 {now.strftime("%Y-%m-%d")}',
                                                _bitable_fields())
            if not table_id:
                return
            flat = {'_ts': int(now.timestamp() * 1000), 'sealed_n': len(sealed),
                    'blast_n': len(blast), 'blast_s': ' / '.join(blast_names) or '-'}
            record = {fd['field_name']: flat.get(fd['key']) for fd in TAIL_FIELDS
                      if flat.get(fd['key']) not in (None, '')}
            self.bw.append_records(cfg.SENTIMENT_BITABLE_APP_TOKEN, table_id, [record])
        except Exception:  # noqa: BLE001
            logger.exception('尾盘写表失败')


if __name__ == '__main__':
    # 合成 candidates 验炸板检测 (上轮封→本轮开)
    from types import SimpleNamespace
    bundle = {'sentiment_raw': {'candidates': [
        {'code': '688020.SH', 'FCAmo': 0, 'ZTPrice': 0, 'Max': 0, 'EverZTCount': 0, 'FCb': 0},
        {'code': '300986.SZ', 'FCAmo': 0, 'ZTPrice': 0, 'Max': 0, 'EverZTCount': 0, 'FCb': 0},
        {'code': '300363.SZ', 'FCAmo': 5000, 'ZTPrice': 0, 'Max': 0, 'EverZTCount': 0, 'FCb': 0},
    ]}}
    mon = TailMonitor(dry_run=True)
    now = datetime.now()
    mon.prev_fcamo = {'688020.SH': 6609, '300986.SZ': 7757, '300363.SZ': 3645}  # 上轮都封
    mon.maybe_push(bundle, now)   # 688020/300986 应判炸板(封→0); 300363 仍封
