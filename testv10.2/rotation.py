"""testv10.2 板块轮动计算层 (rotation) — 强度榜 + 切换事件 → 飞书表

脚本路径: K:/QuestDB_test/testv10.2/rotation.py
传导链 ②板块跟风 (强度上升) + ③强弱分化 (rank 变化)。
消费 meso rows (按 level 分组), 跨轮 history 算 rank delta/score 加速度。
sector (行业级 '三级') / concept (概念级 '概念') = 两个 RotationMonitor 实例。
计算层: 零 tq 调用, 只消费 rows; 飞书表复用 SENTIMENT_BITABLE_APP_TOKEN (同多维表格不同子表)。
红线: dry_run 默认; 门控; 写表失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

# 轮动表字段 (sector/concept 共用; 类型字段区分)
_LABEL_OPTS = [{'name': '行业', 'color': 3}, {'name': '概念', 'color': 0}]
ROTATION_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '类型', 'type': 3, 'key': '_label', 'options': _LABEL_OPTS},
    {'field_name': 'Top1板块', 'type': 1, 'key': '_t1'},
    {'field_name': 'Top1涨幅%', 'type': 2, 'key': '_t1zaf'},
    {'field_name': 'Top1涨停', 'type': 2, 'key': '_t1zt'},
    {'field_name': 'Top1动能', 'type': 2, 'key': '_t1sc'},
    {'field_name': 'Top2板块', 'type': 1, 'key': '_t2'},
    {'field_name': 'Top3板块', 'type': 1, 'key': '_t3'},
    {'field_name': 'Bottom1板块', 'type': 1, 'key': '_b1'},
    {'field_name': 'Bottom1涨幅%', 'type': 2, 'key': '_b1zaf'},
    {'field_name': '新晋Top3', 'type': 1, 'key': 'risers_s'},
    {'field_name': '加速板', 'type': 1, 'key': 'accel_s'},
    {'field_name': '切换数', 'type': 2, 'key': 'switch_n'},
]


def _bitable_fields() -> list:
    out = []
    for fd in ROTATION_FIELDS:
        d = {'field_name': fd['field_name'], 'type': fd['type']}
        if 'options' in fd:
            d['options'] = fd['options']
        out.append(d)
    return out


class RotationMonitor:
    """板块轮动: 强度榜 (Top/Bottom by score) + 切换 (rank delta/score 加速) → 飞书表。

    sector/concept = 不同 level_filter 的实例。"""

    def __init__(self, level_filter: set[str], label: str, table_base: str,
                 dry_run: bool | None = None):
        self.level_filter = level_filter     # 包含的 level 集合 (如 {'概念'} 或 {'三级'})
        self.label = label                   # '行业' / '概念' (飞书类型字段 + 日志)
        self.table_base = table_base         # 'v10.2行业轮动' / 'v10.2概念轮动'
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.prev_score: dict[str, float] = {}   # 跨轮: code -> 上轮 score
        self.prev_rank: dict[str, int] = {}      # 跨轮: code -> 上轮 rank
        self.last_push_ts: datetime | None = None
        self._bw = None
        self.last_result: dict = {}   # 供 alert_engine 读

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    def maybe_push(self, rows: list[dict], now: datetime) -> bool:
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.ROTATION_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        try:
            result = self.compute(rows)
        except Exception:  # noqa: BLE001
            logger.exception('{}轮动 compute 失败, 跳过', self.label)
            return False
        self._write(result, now)
        self.last_result = result
        return True

    def force_push(self, rows: list[dict], now: datetime) -> bool:
        """绕门控写一行 (收盘定格用)。"""
        try:
            result = self.compute(rows)
        except Exception:  # noqa: BLE001
            logger.exception('{}轮动 force_push 失败', self.label)
            return False
        self._write(result, now)
        return True

    def compute(self, rows: list[dict]) -> dict:
        grp = [r for r in rows if r.get('level') in self.level_filter]
        ranked = sorted(grp, key=lambda r: r.get('score', 0), reverse=True)
        cur_rank = {r['code']: i + 1 for i, r in enumerate(ranked)}
        top = ranked[:cfg.ROTATION_TOPN]
        bot = sorted(grp, key=lambda r: r.get('score', 0))[:3] if grp else []

        # 切换: rank 上升≥阈值 (新晋) / score 加速≥阈值
        risers: list = []   # (name, prev_rank, cur_rank)
        accel: list = []    # (name, score_delta)
        for r in ranked[:cfg.ROTATION_TOPN + 5]:
            c = r['code']
            pr = self.prev_rank.get(c)
            if pr and pr - cur_rank[c] >= cfg.ROTATION_RANK_DELTA:
                risers.append((r['name'], pr, cur_rank[c]))
            delta = r.get('score', 0) - self.prev_score.get(c, r.get('score', 0))
            if delta >= cfg.ROTATION_ACCEL:
                accel.append((r['name'], round(delta, 1)))
        switch_n = len(risers) + len(accel)
        # 更新跨轮
        self.prev_score = {r['code']: r.get('score', 0) for r in grp}
        self.prev_rank = cur_rank

        def _g(i, key, default=0):
            return top[i].get(key, default) if len(top) > i else default
        return {
            '_label': self.label,
            '_t1': top[0]['name'] if top else '',
            '_t1zaf': round(_g(0, 'ZAF'), 2), '_t1zt': _g(0, 'ZTGPNum'),
            '_t1sc': round(_g(0, 'score'), 1),
            '_t2': top[1]['name'] if len(top) > 1 else '',
            '_t3': top[2]['name'] if len(top) > 2 else '',
            '_b1': bot[0]['name'] if bot else '',
            '_b1zaf': round(bot[0].get('ZAF', 0), 2) if bot else 0,
            'risers_s': ' / '.join(f'{n}({p}→{c})' for n, p, c in risers[:3]) or '-',
            'accel_s': ' / '.join(f'{n}+{d}' for n, d in accel[:3]) or '-',
            'switch_n': switch_n,
            'group_n': len(grp),
        }

    def _write(self, result: dict, now: datetime) -> None:
        logger.info('🔄 {}轮动 | {} | Top: {} {:.1f}% 涨停{} | 新晋:{} 加速:{} 切换{}',
                    self.label, now.strftime('%H:%M'),
                    result['_t1'], result['_t1zaf'], result['_t1zt'],
                    result['risers_s'], result['accel_s'], result['switch_n'])
        if self.dry_run:
            return
        try:
            self._write_bitable(result, now)
        except Exception:  # noqa: BLE001
            logger.exception('{}轮动写表失败', self.label)

    def _write_bitable(self, result: dict, now: datetime) -> None:
        app_token = cfg.SENTIMENT_BITABLE_APP_TOKEN
        if not app_token:
            logger.warning('{}轮动: 无 app_token (先跑 sentiment --write 建表), 跳过写表', self.label)
            return
        table_id = self.bw.auto_named_table(app_token, f'{self.table_base} {now.strftime("%Y-%m-%d")}',
                                            _bitable_fields())
        if not table_id:
            logger.error('{}轮动 auto_named_table 失败', self.label)
            return
        flat = dict(result)
        flat['_ts'] = int(now.timestamp() * 1000)
        record = {fd['field_name']: flat.get(fd['key']) for fd in ROTATION_FIELDS
                  if flat.get(fd['key']) not in (None, '')}
        ok = self.bw.append_records(app_token, table_id, [record])
        logger.info('{}轮动写表 {}', self.label, '✅' if ok else '❌')


if __name__ == '__main__':
    # 自检: 合成 meso rows (概念+三级) 验强度榜+切换渲染
    from types import SimpleNamespace
    rows = [
        {'code': '880501.SH', 'name': '芯片', 'level': '概念', 'score': 75, 'ZAF': 4.2, 'ZTGPNum': 12},
        {'code': '880502.SH', 'name': 'PCB', 'level': '概念', 'score': 68, 'ZAF': 3.5, 'ZTGPNum': 8},
        {'code': '881301.SH', 'name': '半导体', 'level': '三级', 'score': 72, 'ZAF': 3.8, 'ZTGPNum': 10},
        {'code': '881302.SH', 'name': '消费电子', 'level': '三级', 'score': 55, 'ZAF': 1.2, 'ZTGPNum': 3},
    ]
    concept = RotationMonitor({'概念'}, '概念', 'v10.2概念轮动', dry_run=True)
    sector = RotationMonitor({'三级'}, '行业', 'v10.2行业轮动', dry_run=True)
    now = datetime.now()
    # 第1轮建基线
    concept.maybe_push(rows, now)
    sector.maybe_push(rows, now)
    print(f'概念轮动: {concept.compute(rows)}')
    print(f'行业轮动: {sector.compute(rows)}')
