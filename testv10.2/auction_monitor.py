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
from datetime import datetime, time as dtime  # noqa: E402

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

    def __init__(self, dry_run: bool | None = None, pub=None, ms=None):
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.pub = pub
        self.ms = ms
        self.last_candidates: list[str] = []   # 一字候选 code (给 open_monitor 订阅)
        self.last_push_ts: datetime | None = None
        self._preview_pushed = False           # 竞价预测卡只推一次
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
        # 竞价预测卡: 9:24 后推一次 (预测今日最强行业/概念/共振板块 + 最强梯队)
        if self.pub and now.time() >= dtime(9, 24) and not self._preview_pushed:
            self._preview_pushed = True
            tb = raw['top_boards']
            cands = raw['candidates']
            lines = self._forecast_lines(raw, tb, cands, now)
            self.pub.on_auction_preview(lines, now)
            # 龙头涨停预测卡: 建议关注个股梯队 (基于一字候选 + 板块强度)
            self.pub.on_zt_forecast(self._zt_forecast_lines(tb, cands, now), now)
        return True

    def _zt_forecast_lines(self, tb: list, cands: list, now: datetime) -> list:
        """龙头涨停预测: 建议关注个股梯队 1-6。

        预测开盘可能封板的龙头 = 一字候选 (竞价涨停买入 OpenZTBuy>0, 竞价抢筹
        开盘大概率封) + 板块强度加权。梯队排序: 一字候选 openzt 降序 (抢筹最凶的
        排前), 带连板 lb + 所属板块 (强势板块的候选优先)。"""
        lines = [f'🚀 龙头涨停预测 | {now.strftime("%H:%M")}', '']
        if not cands:
            lines.append('建议关注: 无一字候选 (竞价无涨停, 观望)')
            return lines
        # 强势板块 code 集 (竞价涨停家数≥2 的, 给候选加权)
        strong_boards = {b['code'] for b in tb if b.get('outside', 0) >= 2}
        # 候选排序: openzt 降序 (竞价抢筹最凶排前), 加连板辅助
        ranked = sorted(cands, key=lambda c: (-c.get('openzt', 0), -c.get('lb', 0)))
        top = ranked[:6]
        # 所属板块 (候选 code → 板块名)
        for i, c in enumerate(top, 1):
            name = self.ms.stock_name(c['code']) if self.ms else c['code']
            boards = []
            if self.ms:
                boards = [self.ms.board_name(b) for b in
                          sorted(self.ms.boards_of(c['code']))[:2]]
            lb = c.get('lb', 0)
            lb_s = f' {int(lb)}连板' if lb >= 2 else ''
            openzt_s = f' 竞价{c["openzt"]/1e4:.0f}万' if c.get('openzt') else ''
            lines.append(f'  {i}. {name}{lb_s}{openzt_s}'
                         + (f'  [{"/".join(boards)}]' if boards else ''))
        lines.append(f'  (竞价抢筹 Top{len(top)}, 开盘盯封板)')
        return lines

    def _forecast_lines(self, raw: dict, tb: list, cands: list, now: datetime) -> list:
        """竞价预测: 行业Top6/概念Top6/共振Top3/最强梯队Top6。

        板块强度 = 竞价涨停家数 Outside 主导 + 昨量比 ratio 辅助 (放量确认)。
        行业/概念按 ms.board_meta 的 行业级别 分类。共振 = 行业∩概念同强。
        最强梯队 = 一字候选 (竞价涨停成分股, OpenZTBuy>0, 给 open 订阅)。"""
        lines = [f'🔮 竞价预测今日最强 | {now.strftime("%H:%M")}', '']
        # 板块分类: 行业(三级) vs 概念
        ind, con = [], []
        for b in tb:
            lv = self.ms.board_meta.get(b['code'], {}).get('行业级别', '') if self.ms else ''
            score = b['outside'] * 100 + b['ratio'] * 10   # 涨停家数主导 + 量比辅助
            name = self.ms.board_name(b['code']) if self.ms else b['code']
            entry = (name, score, b['outside'], b['ratio'])
            (ind if lv == '三级' else con).append(entry)
        ind.sort(key=lambda x: -x[1]); con.sort(key=lambda x: -x[1])
        ind_s = ' '.join(f'{n}({o}停)' for n, s, o, r in ind[:6]) or '-'
        con_s = ' '.join(f'{n}({o}停)' for n, s, o, r in con[:6]) or '-'
        lines.append(f'行业板块: {ind_s}')
        lines.append(f'概念板块: {con_s}')
        # 共振: 行业Top3 与 概念Top3 题材重叠 (PCB vs PCB概念 = 包含关系)
        ind3 = [(n, s) for n, s, o, r in ind[:3]]
        con3 = [(n, s) for n, s, o, r in con[:3]]
        reso = []
        for iname, iscore in ind3:
            for cname, cscore in con3:
                # 题材关联: 概念名包含行业名 或 行业名包含概念名 (去 '概念' 后缀)
                i_kw = iname.replace('概念', '')
                c_kw = cname.replace('概念', '')
                if i_kw and c_kw and (i_kw in c_kw or c_kw in i_kw):
                    reso.append(f'{iname}+{cname}')
                    break
        lines.append(f'共振板块: ' + (' '.join(reso[:3]) if reso else '-'))
        # 最强梯队: 一字候选 (按 openzt 降序)
        if cands:
            c_s = ' '.join(f'{self.ms.stock_name(c["code"]) if self.ms else c["code"]}'
                           for c in cands[:6])
            lines.append(f'最强梯队: {c_s}')
        else:
            lines.append('最强梯队: 无一字候选 (竞价无涨停)')
        return lines

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
