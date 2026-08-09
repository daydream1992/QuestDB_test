"""testv10.2 竞价监控 (auction) — 放量板块榜 + 一字候选 → 飞书表 + 给 open_monitor

脚本路径: K:/QuestDB_test/testv10.2/auction_monitor.py
时段: 9:15-9:25 (stage='auction')
消费 data_provider 的 auction_raw (板块竞价 Outside/OpenAmo + 成分股 OpenZTBuy 一字候选)。
产出: 飞书表 v10.2竞价 + last_candidates (一字候选 code, 供 open_monitor 9:30 订阅)。
计算层: 零 tq 调用 (raw 来自采集层)。红线: dry_run 默认; 门控; 写表失败不崩。
注: 竞价字段(OpenAmo/OpenZTBuy)盘外为昨日残留, 真实值需盘中 9:15 验证。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

AUCTION_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '竞价涨停板块数', 'type': 2, 'key': 'zt_board_n'},
    {'field_name': 'Top1放量板块', 'type': 1, 'key': '_tb1'},
    {'field_name': 'Top1竞价昨量比', 'type': 2, 'key': '_tb1ratio'},
    {'field_name': 'Top2板块', 'type': 1, 'key': '_tb2'},
    {'field_name': 'Top3板块', 'type': 1, 'key': '_tb3'},
    {'field_name': '一字候选数', 'type': 2, 'key': 'cand_n'},
    {'field_name': 'Top1候选', 'type': 1, 'key': '_c1'},
    {'field_name': 'Top2候选', 'type': 1, 'key': '_c2'},
    {'field_name': 'Top3候选', 'type': 1, 'key': '_c3'},
    {'field_name': '采集耗时秒', 'type': 2, 'key': 'duration'},
]


def _bitable_fields() -> list:
    return [{'field_name': fd['field_name'], 'type': fd['type']}
            for fd in AUCTION_FIELDS]


class AuctionMonitor:
    """竞价: 放量板块榜 + 一字候选 → 飞书表; last_candidates 供 open_monitor。"""

    def __init__(self, dry_run: bool | None = None):
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.last_candidates: list[str] = []   # 一字候选 code (给 open_monitor 订阅)
        self.last_push_ts: datetime | None = None
        self._bw = None

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    def maybe_push(self, bundle: dict, now: datetime) -> bool:
        if 'auction_raw' not in bundle:
            return False
        if self.last_push_ts is not None and \
                (now - self.last_push_ts).total_seconds() < cfg.AUCTION_INTERVAL_SEC:
            return False
        self.last_push_ts = now
        raw = bundle['auction_raw']
        self.last_candidates = [c['code'] for c in raw['candidates']]
        self._write(raw, now)
        return True

    def _write(self, raw: dict, now: datetime) -> None:
        tb = raw['top_boards']
        cands = raw['candidates']
        logger.info('📋 竞价 | {} | 涨停板块{}只 一字候选{}只 采集{:.0f}s | 放量Top(昨量比): {}',
                    now.strftime('%H:%M'), raw['zt_board_n'], len(cands), raw['duration'],
                    ' / '.join(f"{b['code']}(×{b['ratio']},{b['open_amo']/1e8:.1f}亿,{b['outside']}停)"
                               for b in tb[:3]) or '-')
        if self.dry_run:
            return
        try:
            self._write_bitable(raw, tb, cands, now)
        except Exception:  # noqa: BLE001
            logger.exception('竞价写表失败')

    def _write_bitable(self, raw, tb, cands, now):
        app_token = cfg.SENTIMENT_BITABLE_APP_TOKEN
        if not app_token:
            logger.warning('竞价: 无 app_token, 跳过写表')
            return
        table_id = self.bw.auto_named_table(app_token, f'v10.2竞价 {now.strftime("%Y-%m-%d")}',
                                            _bitable_fields())
        if not table_id:
            logger.error('竞价 auto_named_table 失败')
            return
        flat = {
            '_ts': int(now.timestamp() * 1000),
            'zt_board_n': raw['zt_board_n'], 'cand_n': len(cands), 'duration': raw['duration'],
            '_tb1': tb[0]['code'] if tb else '', '_tb1ratio': tb[0]['ratio'] if tb else 0,
            '_tb2': tb[1]['code'] if len(tb) > 1 else '',
            '_tb3': tb[2]['code'] if len(tb) > 2 else '',
            '_c1': cands[0]['code'] if cands else '',
            '_c2': cands[1]['code'] if len(cands) > 1 else '',
            '_c3': cands[2]['code'] if len(cands) > 2 else '',
        }
        record = {fd['field_name']: flat.get(fd['key']) for fd in AUCTION_FIELDS
                  if flat.get(fd['key']) not in (None, '')}
        ok = self.bw.append_records(app_token, table_id, [record])
        logger.info('竞价写表 {}', '✅' if ok else '❌')


if __name__ == '__main__':
    # 盘外验证: 拉一次竞价采集 (字段昨日残留), 验结构 + candidates。盘中 9:15 跑看真实值。
    from lib.tq_client import init, close  # noqa: E402
    import data_provider  # noqa: E402

    init()
    try:
        mon = AuctionMonitor(dry_run=True)
        raw = data_provider.fetch_auction_raw()
        mon._write(raw, datetime.now())
        print(f'\n一字候选 ({len(mon.last_candidates)}): {mon.last_candidates[:10]}')
    finally:
        close()
