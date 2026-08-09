"""testv10.2 推送层 (publisher) — 4 种卡片 + ≤2/min 令牌桶

脚本路径: K:/QuestDB_test/testv10.2/publisher.py
卡片: ①🏆主线梯队(汇总,变化门控) ②🟢主线首封(事件≤2/min) ③🔴主线退潮(事件≤2/min) ④⚡v10.1式盘点(汇总,30分)
依赖: urllib (飞书 webhook), lib.tq_client (副作用入路径)
红线: dry_run 默认 True; 无 webhook 只 log; 事件通道 ≤2/min。
"""

import bootstrap
bootstrap.ensure_paths()

import json  # noqa: E402
import os  # noqa: E402
from collections import deque  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from urllib import request as urlrequest  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

import lib.tq_client as _tqc  # noqa: E402,F401  副作用: TQCENTER_PATH 入 sys.path
import settings as cfg  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', '.env'))
_WEBHOOK = os.getenv('LARK_WEBHOOK_URL', '')
_CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'


class TokenBucket:
    """令牌桶: 支持分桶 (机会类/预警类各 ≤1/min, 共 2/min 人类注意力红线)。
    机会类 (新主线/趋势确认/龙头封板) 与预警类 (跳水/炸板/回封) 分开,
    避免负向事件挤光配额饿死正向机会卡。"""

    def __init__(self, max_per_min: int, lanes: int = 2):
        self.max = max_per_min
        self.lanes = lanes
        # 每 lane 独立 deque: 均分 max_per_min (如 2/min ÷ 2 lanes = 每桶 1/min)
        self._times: list[deque] = [deque() for _ in range(lanes)]

    def allow(self, now: datetime, lane: int = 0) -> bool:
        lane = lane % self.lanes
        q = self._times[lane]
        cutoff = now - timedelta(seconds=60)
        while q and q[0] < cutoff:
            q.popleft()
        per_lane = max(1, self.max // self.lanes)
        if len(q) < per_lane:
            q.append(now)
            return True
        return False


def _send(title: str, body_lines: list[str], dry_run: bool) -> bool:
    card = {'header': {'title': {'tag': 'plain_text', 'content': title}},
            'elements': [{'tag': 'div',
                          'text': {'tag': 'lark_md', 'content': '\n'.join(body_lines)}}]}
    payload = {'msg_type': 'interactive', 'card': card}
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if dry_run or not _WEBHOOK:
        logger.info('[dry-run] {} ({}B)\n{}', title, len(body), '\n'.join(body_lines[:12]))
        return True
    try:
        req = urlrequest.Request(_WEBHOOK, data=body,
                                 headers={'Content-Type': 'application/json'})
        with urlrequest.urlopen(req, timeout=cfg.WEBHOOK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('code', 0) != 0:
                logger.warning('飞书业务错误: {}', data)
                return False
            logger.info('✈ 推送 OK: {} ({}B)', title, len(body))
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning('飞书推送失败: {}', e)
        return False


class Publisher:

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.bucket = TokenBucket(cfg.PUSH_TEXT_MAX_PER_MIN)

    # 旧梯队/首封/退潮/盘点卡 (on_summary/on_first_seal/on_ebb/on_v101_summary) 随 signal_extractor 砍除
    # 现用: on_dive_alert (alert_engine 大盘跳水) / on_open_surge (open_monitor 开盘拉升) / on_blast_alert (尾盘炸板)

    # 🔴 大盘跳水联动预警 (L1大盘→L2板块→L3个股; 事件, ≤2/min)
    def on_dive_alert(self, payload: dict, now: datetime) -> bool:
        if not self.bucket.allow(now, lane=1):
            return False
        lines = [f'🔴 大盘跳水预警 | {now.strftime("%H:%M")}', '',
                 '触发: ' + ' · '.join(payload['reasons'])]
        cur = payload['cur']; past = payload['past']
        lines.append(f'综合分 {past["score"]:.0f}→{cur["score"]:.0f} · '
                     f'封板率 {past["fbl"]:.0f}%→{cur["fbl"]:.0f}% · '
                     f'炸板 {past["blasted"]}→{cur["blasted"]}')
        if payload.get('boards'):
            lines.append(f'▼ 领跌板块 (L2)')
            for name, zaf, state in payload['boards']:
                lines.append(f'├─ {name} {zaf:+.1f}% [{state}]')
        if payload.get('stocks'):
            lines.append(f'▼ 个股梯队异动 (L3)')
            for name, zaf, role in payload['stocks']:
                lines.append(f'├─ {name} {zaf:+.1f}% {role}')
        return _send(f'🔴 大盘跳水预警 | {now.strftime("%H:%M")}', lines, self.dry_run)

    # ⑥ 🚀 开盘拉升 (传导链①龙头异动; 事件, ≤2/min bucket; send_warn 另走客户端)
    def on_open_surge(self, code: str, name: str, price: float, rise: float,
                      now: datetime) -> bool:
        if not self.bucket.allow(now, lane=1):
            return False
        lines = [f'🚀 开盘拉升 | {now.strftime("%H:%M")}', '',
                 f'{name}({code}) {price:.2f}  相对开盘 {rise:+.1f}%']
        return _send(f'🚀 开盘拉升 {name}', lines, self.dry_run)

    # ⑦ 💥 个股炸板预警 (tail 段; 事件, ≤2/min)
    def on_blast_alert(self, tail: dict, now: datetime) -> bool:
        if not self.bucket.allow(now, lane=1):
            return False
        lines = [f'💥 尾盘炸板预警 | {now.strftime("%H:%M")}', '',
                 f'封板候选 {tail.get("sealed_n", 0)} 只, 炸板 {tail.get("blast_n", 0)} 只',
                 tail.get('blast_s', '-')]
        return _send(f'💥 尾盘炸板 | {now.strftime("%H:%M")}', lines, self.dry_run)

    # 💥 个股炸板实时 (blindspot 盲区 6s 粒度; FCAmo 封→开瞬间; 盘中/tail)
    def on_seal_break(self, code: str, name: str, prev: float,
                      break_n: int = 0, now: datetime | None = None) -> bool:
        if not self.bucket.allow(now, lane=1):
            return False
        lines = [f'💥 炸板 | {now.strftime("%H:%M")}', '',
                 f'{name}({code})  封单 {prev:.0f}万→0'
                 + (f'  [今日炸板{break_n}次]' if break_n else '')]
        return _send(f'💥 炸板 {name}', lines, self.dry_run)

    # 🔁 炸板回封实时 (blindspot 盲区 6s 粒度; FCAmo 开→封瞬间)
    def on_seal_back(self, code: str, name: str, cur: float,
                     back_n: int = 0, now: datetime | None = None) -> bool:
        if not self.bucket.allow(now, lane=1):
            return False
        lines = [f'🔁 回封 | {now.strftime("%H:%M")}', '',
                 f'{name}({code})  回封 {cur:.0f}万'
                 + (f'  [今日回封{back_n}次]' if back_n else '')]
        return _send(f'🔁 回封 {name}', lines, self.dry_run)

    # ============ 机会类事件 (盘中决策最缺; 与负向共用 ≤2/min bucket) ============

    # 🟢 板块新主线入池 (状态机 NEW; 60s内发现新方向, 系统最大价值)
    def on_board_new(self, boards: list, now: datetime) -> bool:
        if not self.bucket.allow(now):
            return False
        lines = [f'🟢 新主线 | {now.strftime("%H:%M")}']
        for b in boards:
            lines.append(f'{b["name"]}  涨幅{b["zaf"]:+.1f}%  涨停{b["zt"]}')
            if b.get('lights'):
                lines.append(f'  [探照灯: {" ".join(b["lights"])}]')
        return _send(f'🟢 新主线 {len(boards)} 板块', lines, self.dry_run)

    # 🔥 板块趋势确认 (连续 N 轮 HOT, 非单轮脉冲)
    def on_hot_streak(self, board: dict, now: datetime) -> bool:
        if not self.bucket.allow(now):
            return False
        lines = [f'🔥 趋势确认 | {now.strftime("%H:%M")}', '',
                 f'{board["name"]}  连续{board["rounds"]}轮HOT  动能分{board["score"]:.0f}',
                 f'涨停 {board["zt_prev"]}→{board["zt_cur"]}']
        return _send(f'🔥 趋势确认 {board["name"]}', lines, self.dry_run)

    # 🚀 龙头封板 (drilled 涨停股; 封单/封成比/首次封板时间)
    def on_limit_up(self, stocks: list, now: datetime) -> bool:
        if not self.bucket.allow(now):
            return False
        lines = [f'🚀 龙头封板 | {now.strftime("%H:%M")}']
        for s in stocks:
            lines.append(f'{s["name"]}  {s["zaf"]:+.1f}%  封单{s["fcamo"]:.0f}万'
                         f'  封成比{s["fcb"]:.2f}')
            if s.get('first_limit'):
                lines.append(f'  ⏱ 首次封板 {s["first_limit"]}')
            if s.get('boards'):
                lines.append(f'  [{" ".join(s["boards"][:3])}]')
        return _send(f'🚀 龙头封板 {len(stocks)} 只', lines, self.dry_run)


if __name__ == '__main__':
    now = datetime.now()
    pub = Publisher(dry_run=True)
    # 🔴 大盘跳水联动预警 (L1+L2+L3)
    pub.on_dive_alert({'reasons': ['封板率90%→45%', '炸板8→31'],
                       'cur': {'score': 38, 'fbl': 45, 'blasted': 31},
                       'past': {'score': 70, 'fbl': 90, 'blasted': 8},
                       'boards': [('PCB概念', -3.2, 'WARN'), ('锂矿', -2.8, 'WARN')],
                       'stocks': [('志特新材', -9.5, '近跌停'), ('方邦股份', -8.0, '炸板/无封')]}, now)
    # 🚀 开盘拉升
    pub.on_open_surge('688020.SH', '方邦股份', 25.8, 5.2, now)
    # 💥 尾盘炸板
    pub.on_blast_alert({'sealed_n': 74, 'blast_n': 2, 'blast_s': '方邦股份(6609→0)'}, now)
    print(f'webhook: {"SET" if _WEBHOOK else "EMPTY"}, dry_run self-test done (3 张新卡)')
