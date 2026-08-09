"""testv10.2 涨停梯队记录器 (ladder_tracker) — 炸板/回封/封板事件落表 + 概念/行业映射

脚本路径: K:/QuestDB_test/testv10.2/ladder_tracker.py
用途: 把 blindspot 检测到的涨停梯队状态变更 (封板/炸板/回封) 持久化到飞书表
      v10.2涨停梯队, 每行带: 时间/代码/名称/所属概念/所属行业/事件类型/封单/
      首次封板时间/封板次数/炸板次数/回封次数。
      交易者扫一眼能看到: 某只股今天封了几次、炸了几次、回封几次, 属于哪个题材。
消费: blindspot.drain_events() 的事件 (6s 回调检测), 事件驱动写表 (非每轮全量)。
板块映射: ms.boards_of(code) → 概念/行业 (行业级别='三级' 归行业, '概念' 归概念)。
红线: dry_run 默认; 写表失败不崩; 复用 SENTIMENT_BITABLE_APP_TOKEN。
"""

import bootstrap
bootstrap.ensure_paths()

import importlib  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

# 事件类型单选 (状态变更 + 衰竭前兆)
_EVENT_OPTS = [{'name': '封板', 'color': 0}, {'name': '炸板', 'color': 1},
               {'name': '回封', 'color': 0}, {'name': '衰竭', 'color': 2}]

LADDER_FIELDS = [
    {'field_name': '时间', 'type': 5, 'key': '_ts'},
    {'field_name': '事件', 'type': 3, 'key': 'event', 'options': _EVENT_OPTS},
    {'field_name': '代码', 'type': 1, 'key': 'code'},
    {'field_name': '名称', 'type': 1, 'key': 'name'},
    {'field_name': '所属概念', 'type': 1, 'key': 'concepts'},
    {'field_name': '所属行业', 'type': 1, 'key': 'industries'},
    {'field_name': '封单(万)', 'type': 2, 'key': 'fcamo'},
    {'field_name': '首次封板', 'type': 1, 'key': 'first_limit'},
    {'field_name': '封板次数', 'type': 2, 'key': 'zt_n'},
    {'field_name': '炸板次数', 'type': 2, 'key': 'break_n'},
    {'field_name': '回封次数', 'type': 2, 'key': 'back_n'},
]


def _bitable_fields() -> list:
    out = []
    for fd in LADDER_FIELDS:
        d = {'field_name': fd['field_name'], 'type': fd['type']}
        if 'options' in fd:
            d['options'] = fd['options']
        out.append(d)
    return out


class LadderTracker:
    """涨停梯队记录器: 事件 → 飞书表 (带计数 + 概念/行业映射)。"""

    def __init__(self, ms=None, dry_run: bool | None = None):
        self.ms = ms
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self._bw = None

    @property
    def bw(self):
        if self._bw is None:
            self._bw = importlib.import_module('feishu.bitable_writer')
        return self._bw

    # 事件驱动: 记录单个状态变更 (封板/炸板/回封)
    def record_event(self, ev: dict, blindspot, now: datetime) -> None:
        """ev: {code, name, type(炸板/回封/衰竭), prev, cur}; blindspot 提供计数/首次封板。"""
        code = ev['code']
        # 事件类型: 炸板/回封/衰竭 (衰竭=封单萎缩前兆, 非状态变更)
        if ev['type'] == '炸板':
            event_type = '炸板'
        elif ev['type'] == '衰竭':
            event_type = '衰竭'
        else:
            event_type = '回封'
        # 封单: 炸板记 prev (炸前封单, 如 6609→0), 回封/衰竭记 cur
        fcamo = ev.get('prev', 0) if ev['type'] == '炸板' else ev.get('cur', 0)
        # 板块映射: 概念/行业分开
        concepts, industries = [], []
        if self.ms:
            for b in sorted(self.ms.boards_of(code)):
                lv = self.ms.board_meta.get(b, {}).get('行业级别', '')
                if lv == '概念':
                    concepts.append(self.ms.board_name(b))
                elif lv == '三级':
                    industries.append(self.ms.board_name(b))
        # 计数 + 首次封板 (从 blindspot)
        zt_n = blindspot.zt_count.get(code, 0)
        break_n = blindspot.break_count.get(code, 0)
        back_n = blindspot.back_count.get(code, 0)
        first_limit = blindspot.first_limit_time.get(code, '')
        name = ev.get('name', code)
        logger.info('📶 涨停梯队 | {} | {} {} ({}): 封单{}万 封板{} 炸{} 回{} | 概念[{}] 行业[{}]',
                    now.strftime('%H:%M:%S'), event_type, name, code, fcamo,
                    zt_n, break_n, back_n,
                    '/'.join(concepts[:3]) or '-', '/'.join(industries[:3]) or '-')
        if self.dry_run:
            return
        try:
            table_id = self.bw.auto_named_table(
                cfg.SENTIMENT_BITABLE_APP_TOKEN,
                f'v10.2涨停梯队 {now.strftime("%Y-%m-%d")}', _bitable_fields())
            if not table_id:
                return
            record = {'时间': int(now.timestamp() * 1000), '事件': event_type,
                      '代码': code, '名称': name,
                      '所属概念': '/'.join(concepts[:5]) or '-',
                      '所属行业': '/'.join(industries[:5]) or '-',
                      '封单(万)': fcamo,
                      '首次封板': first_limit or '-',
                      '封板次数': zt_n, '炸板次数': break_n, '回封次数': back_n}
            # 空值剔除 (单选事件除外)
            rec = {k: v for k, v in record.items() if v not in (None, '', '-')}
            rec['事件'] = event_type
            self.bw.append_records(cfg.SENTIMENT_BITABLE_APP_TOKEN, table_id, [rec])
        except Exception:  # noqa: BLE001
            logger.exception('涨停梯队写表失败')


if __name__ == '__main__':
    # 自检: 合成炸板/回封事件 + blindspot 计数, 验板块映射 + 记录 (dry-run)
    from types import SimpleNamespace as NS
    tr = LadderTracker(dry_run=True)
    class FakeMs:
        def __init__(self):
            self.board_meta = {'880001.SH': {'行业级别': '概念', '板块名称': 'PCB概念'},
                               '881334.SH': {'行业级别': '三级', '板块名称': 'PCB'},
                               '880952.SH': {'行业级别': '概念', '板块名称': '5G概念'}}
        def boards_of(self, code):
            return {'880001.SH', '881334.SH', '880952.SH'}
        def board_name(self, b):
            return self.board_meta.get(b, {}).get('板块名称', b)
    tr.ms = FakeMs()
    bs = NS(zt_count={'688020.SH': 3}, break_count={'688020.SH': 2},
            back_count={'688020.SH': 1},
            first_limit_time={'688020.SH': '10:32:05'})
    now = datetime.now()
    tr.record_event({'code': '688020.SH', 'name': '方邦股份', 'type': '炸板',
                     'prev': 6609.0, 'cur': 0}, bs, now)
    tr.record_event({'code': '688020.SH', 'name': '方邦股份', 'type': '回封',
                     'prev': 0, 'cur': 7757.0}, bs, now)
    print('LADDER TRACKER SELF-TEST PASSED')
