"""testv10.2 个股排名 (stock_ranking) — 池内活跃股综合排名 → 飞书表 (打板选股底座)

脚本路径: K:/QuestDB_test/testv10.2/stock_ranking.py
消费 drilled (hot板TopN成分股, 池内非全市场 — 全市场全钻 80s 不可接受)。
综合分 = 归一化加权 (涨幅 + 封单 + 量比 + 资金 + 位置)。
计算层: 零 tq (drilled 来自 ticker)。红线: dry_run 默认; 门控; 写表失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

# 归一化区间 (lo, hi, 权重); 盘中校准
# 单位铁证 (探针 2026-08-10): FCAmo/Zjl_HB 均**万元** (非元); ZAF→limit_pct 区分 10/20/30cm
NORM_W = [
    ('limit_pct',  0.0, 1.0,  0.30),    # 涨停比例 (涨幅/涨停幅度, 区分主板10/科创创业20/北证30)
    ('FCAmo',      0.0, 1e4,  0.25),    # 封单额 (万元; 1e4万=1亿 封板强)
    ('fLianB',     0.0, 5.0,  0.15),    # 量比 (资金介入)
    ('Zjl_HB',    -3e4, 3e4,  0.15),    # 主力净流入 (万元; ±3亿, 探针实测±2.5亿; 非 Zjl 主买净额口径)
    ('pos_ratio',  0.5, 1.0, 0.15),     # 距高位置 (>0.5 接近高位)
]

RANKING_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': 'Top1', 'type': 1, 'key': '_s1'},
    {'field_name': 'Top1分', 'type': 2, 'key': '_s1sc'},
    {'field_name': 'Top2', 'type': 1, 'key': '_s2'},
    {'field_name': 'Top3', 'type': 1, 'key': '_s3'},
    {'field_name': 'Top4', 'type': 1, 'key': '_s4'},
    {'field_name': 'Top5', 'type': 1, 'key': '_s5'},
    {'field_name': '池内股数', 'type': 2, 'key': 'pool_n'},
]


def _norm(v, lo, hi):
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _bitable_fields():
    return [{'field_name': fd['field_name'], 'type': fd['type']} for fd in RANKING_FIELDS]


class StockRanking:
    """池内个股综合排名 → 飞书表 Top5 (打板选股参考)。"""

    def __init__(self, ms=None, dry_run: bool | None = None):
        self.ms = ms
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.last_push_ts: datetime | None = None
        self._bw = None

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    def maybe_push(self, drilled: dict, now: datetime) -> bool:
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.RANKING_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        ranked = self._rank(drilled)
        self._write(ranked, now)
        return True

    def _rank(self, drilled: dict) -> list:
        out = []
        for code, d in drilled.items():
            score = sum(w * _norm(d.get(k, 0) or 0, lo, hi) for k, lo, hi, w in NORM_W) * 100
            name = self.ms.stock_name(code) if self.ms else code
            out.append((code, name, round(score, 1)))
        out.sort(key=lambda x: x[2], reverse=True)
        return out

    def _write(self, ranked, now):
        top5 = ranked[:5]
        logger.info('📊 个股榜 | {} | 池内{}只 Top: {}',
                    now.strftime('%H:%M'), len(ranked),
                    ' / '.join(f'{n}{s:.0f}' for _, n, s in top5) or '-')
        if self.dry_run or not cfg.SENTIMENT_BITABLE_APP_TOKEN:
            return
        try:
            table_id = self.bw.auto_named_table(cfg.SENTIMENT_BITABLE_APP_TOKEN,
                                                f'v10.2个股榜 {now.strftime("%Y-%m-%d")}',
                                                _bitable_fields())
            if not table_id:
                return
            flat = {'_ts': int(now.timestamp() * 1000), 'pool_n': len(ranked)}
            for i, (_, name, sc) in enumerate(top5, 1):
                flat[f'_s{i}'] = name
                if i == 1:
                    flat['_s1sc'] = sc
            record = {fd['field_name']: flat.get(fd['key']) for fd in RANKING_FIELDS
                      if flat.get(fd['key']) not in (None, '')}
            self.bw.append_records(cfg.SENTIMENT_BITABLE_APP_TOKEN, table_id, [record])
        except Exception:  # noqa: BLE001
            logger.exception('个股榜写表失败')


if __name__ == '__main__':
    drilled = {
        # 20cm 科创板: 涨停比例 1.0 (而非截断 20%), 封单 6609万, 主力净流入 +15375万
        '688020.SH': {'limit_pct': 1.0, 'FCAmo': 6.6e3, 'fLianB': 2.5, 'Zjl_HB': 1.5e4, 'pos_ratio': 0.9},
        # 10cm 主板涨停: 涨停比例 1.0 (与 20cm 涨停等价)
        '300986.SZ': {'limit_pct': 1.0, 'FCAmo': 7.7e3, 'fLianB': 1.3, 'Zjl_HB': 2.5e4, 'pos_ratio': 0.7},
        # 非涨停半途: 涨停比例 0.5
        '300363.SZ': {'limit_pct': 0.5, 'FCAmo': 0, 'fLianB': 1.0, 'Zjl_HB': -1e4, 'pos_ratio': 0.5},
    }
    rk = StockRanking(dry_run=True)
    ranked = rk._rank(drilled)
    print('个股榜:', [(n, s) for _, n, s in ranked])
