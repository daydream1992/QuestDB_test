"""testv10.2 板块内个股梯队 (board_leaderboard) — 龙头/助攻/跟风 → 飞书表

脚本路径: K:/QuestDB_test/testv10.2/board_leaderboard.py
用途: 交易者痛点2"板块内个股梯队(低→高)没做出来"。对每个 HOT/NEW 板块,
      用 drilled 该板块成分股排序 (连板高 > 封板 > 首封早 > 封单大), 打角色标签:
      龙头 (连板最高或首封最早+封单强) / 助攻 (封单>0 次强) / 跟风 (未封涨幅>5%)。
落地: 飞书表 v10.2板块梯队 (3min/行, 行=板块, 列=龙头/助攻/跟风), 中文名。
原料: drilled (EverZTCount/FCAmo/FCb/ZAF) + blindspot.first_limit_time (首封 6s)
      + ms.stocks_of(board) 分组。零新采集。
红线: dry_run 默认; 写表失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

LEADERBOARD_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '板块', 'type': 1, 'key': 'board'},
    {'field_name': '涨停数', 'type': 2, 'key': 'zt'},
    {'field_name': '龙头', 'type': 1, 'key': 'leader'},
    {'field_name': '龙头连板', 'type': 2, 'key': 'leader_lb'},
    {'field_name': '龙头封单万', 'type': 2, 'key': 'leader_fc'},
    {'field_name': '龙头首封', 'type': 1, 'key': 'leader_fl'},
    {'field_name': '助攻', 'type': 1, 'key': 'assist'},
    {'field_name': '跟风数', 'type': 2, 'key': 'follow_n'},
    {'field_name': '跟风', 'type': 1, 'key': 'follow'},
]


def _bitable_fields() -> list:
    return [{'field_name': fd['field_name'], 'type': fd['type']} for fd in LEADERBOARD_FIELDS]


class BoardLeaderboard:
    """板块内个股梯队: 板内排序打角色 → 飞书表。"""

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

    def maybe_push(self, pool, drilled: dict, blindspot, rows: list[dict],
                   now: datetime) -> bool:
        """每 3min 落板内梯队。行=每个 HOT/NEW 板块。"""
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.LEADERBOARD_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        try:
            records = self._build_records(pool, drilled, blindspot, rows, now)
        except Exception:  # noqa: BLE001
            logger.exception('板块梯队 compute 失败, 跳过')
            return False
        self._write(records, now)
        return True

    def _build_records(self, pool, drilled, blindspot, rows, now) -> list:
        """池内 HOT/NEW 板块 → 板内梯队。"""
        rmap = {r['code']: r for r in rows}
        recs = []
        for st in pool.snapshot():
            if st.state not in ('HOT', 'NEW'):
                continue
            bc = st.code
            # 板内成分股 (drilled 中属于该板的)
            bst = {c: d for c, d in drilled.items()
                   if self.ms and bc in self.ms.boards_of(c)}
            if not bst:
                continue
            # 排序: 连板高 > 封板(FCAmo>0) > 首封早 > 封单大
            def _key(item):
                c, d = item
                fl = blindspot.first_limit_time.get(c, '') if blindspot else ''
                return (d.get('EverZTCount', 0), 1 if d.get('FCAmo', 0) > 0 else 0,
                        fl, d.get('FCAmo', 0))
            ranked = sorted(bst.items(), key=_key, reverse=True)
            # 龙头: 第1 (连板最高+封板)
            lc, ld = ranked[0]
            leader = self.ms.stock_name(lc) if self.ms else lc
            leader_lb = int(ld.get('EverZTCount', 0))
            leader_fc = ld.get('FCAmo', 0)
            leader_fl = blindspot.first_limit_time.get(lc, '') if blindspot else ''
            # 助攻: 剩余封单>0 的前 2
            assist = []
            for c, d in ranked[1:]:
                if d.get('FCAmo', 0) > 0 and len(assist) < 2:
                    assist.append(self.ms.stock_name(c) if self.ms else c)
            # 跟风: 未封但涨幅>5%
            follow = []
            for c, d in ranked[1:]:
                if d.get('FCAmo', 0) <= 0 and d.get('ZAF', 0) >= 5 and len(follow) < 3:
                    follow.append(self.ms.stock_name(c) if self.ms else c)
            r = rmap.get(bc, {})
            recs.append({
                'board': r.get('name', bc), 'zt': int(r.get('ZTGPNum', 0)),
                'leader': leader, 'leader_lb': leader_lb, 'leader_fc': leader_fc,
                'leader_fl': leader_fl, 'assist': ' '.join(assist) or '-',
                'follow_n': len(follow), 'follow': ' '.join(follow) or '-',
            })
        return recs

    def _write(self, records: list, now: datetime) -> None:
        if not records:
            return
        logger.info('📊 板块梯队 | {} | {} 板: {}', now.strftime('%H:%M'), len(records),
                    ' / '.join(r['board'] for r in records[:3]))
        if self.dry_run or not cfg.SENTIMENT_BITABLE_APP_TOKEN:
            return
        try:
            table_id = self.bw.auto_named_table(
                cfg.SENTIMENT_BITABLE_APP_TOKEN,
                f'v10.2板块梯队 {now.strftime("%Y-%m-%d")}', _bitable_fields())
            if not table_id:
                return
            rows = []
            for r in records:
                rows.append({'时间': int(now.timestamp() * 1000),
                             '板块': r['board'], '涨停数': r['zt'],
                             '龙头': r['leader'], '龙头连板': r['leader_lb'],
                             '龙头封单万': r['leader_fc'], '龙头首封': r['leader_fl'],
                             '助攻': r['assist'], '跟风数': r['follow_n'],
                             '跟风': r['follow']})
            self.bw.append_records(cfg.SENTIMENT_BITABLE_APP_TOKEN, table_id, rows)
        except Exception:  # noqa: BLE001
            logger.exception('板块梯队写表失败')


if __name__ == '__main__':
    # 自检: 合成池/drilled/blindspot 验梯队排序 (dry-run 打印)
    from types import SimpleNamespace as NS
    bl = BoardLeaderboard(dry_run=True)
    class FakeMs:
        def stock_name(self, c): return {'A': '龙头A', 'B': '助攻B', 'C': '跟风C'}.get(c, c)
        def boards_of(self, c): return {'880001.SH'}   # 都在一个板
    bl.ms = FakeMs()
    class FakePool:
        def snapshot(self):
            return [NS(code='880001.SH', name='创新药', state='HOT')]
    drilled = {'A': {'EverZTCount': 3, 'FCAmo': 8000, 'ZAF': 10},
               'B': {'EverZTCount': 1, 'FCAmo': 5000, 'ZAF': 10},
               'C': {'EverZTCount': 1, 'FCAmo': 0, 'ZAF': 8}}
    bs = NS(first_limit_time={'A': '09:31:00'})
    rows = [{'code': '880001.SH', 'name': '创新药', 'ZTGPNum': 3}]
    recs = bl._build_records(FakePool(), drilled, bs, rows, datetime.now())
    for r in recs:
        print(f'板块 {r["board"]}: 龙头={r["leader"]}({r["leader_lb"]}连板/{r["leader_fc"]}万) '
              f'首封{r["leader_fl"]} 助攻={r["assist"]} 跟风={r["follow"]}')
    assert recs[0]['leader'] == '龙头A', '龙头应是最强'
    assert '助攻B' in recs[0]['assist'], '助攻应含B'
    print('BOARD LEADERBOARD SELF-TEST PASSED')
