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
        self.send_ok = 0        # 推送成功计数 (健康度汇总用)
        self.send_fail = 0      # 推送失败计数 (飞书挂可查)
        self.bucket_dropped = 0 # 限频丢弃计数

    def _dispatch(self, title: str, lines: list, now: datetime, lane: int = 0) -> bool:
        """统一发送入口: bucket 检查 (带丢弃日志) + 计数。各卡调用。"""
        if not self.bucket.allow(now, lane=lane):
            self.bucket_dropped += 1
            logger.debug('限频丢弃 (≤{}/min): {}', self.bucket.max, title)
            return False
        ok = _send(title, lines, self.dry_run)
        if ok:
            self.send_ok += 1
        else:
            self.send_fail += 1
            logger.warning('推送失败计数 {}: {}', self.send_fail, title)
        return ok

    # 旧梯队/首封/退潮/盘点卡 (on_summary/on_first_seal/on_ebb/on_v101_summary) 随 signal_extractor 砍除
    # 现用: on_dive_alert (alert_engine 大盘跳水) / on_open_surge (open_monitor 开盘拉升) / on_blast_alert (尾盘炸板)

    # 🔴 大盘跳水联动预警 (L1大盘→L2板块→L3个股; 事件, ≤2/min)
    def on_dive_alert(self, payload: dict, now: datetime) -> bool:
        lines = [f'🔴 大盘跳水预警 | {now.strftime("%H:%M")}', '',
                 '触发: ' + ' · '.join(payload['reasons'])]
        cur = payload['cur']; past = payload['past']
        # 情绪绝对档位: 70→38 裸分人不知冰点/中性, 加 label 定调
        cur_label = cur.get('label', '')
        lbl = f' [{cur_label}]' if cur_label else ''
        max_lb = cur.get('max_lb', 0)
        mlb = f'  最高连板{int(max_lb)}板' if max_lb else ''
        lines.append(f'综合分 {past["score"]:.0f}→{cur["score"]:.0f}{lbl} · '
                     f'封板率 {past["fbl"]:.0f}%→{cur["fbl"]:.0f}% · '
                     f'炸板 {past["blasted"]}→{cur["blasted"]}{mlb}')
        # 防守话术 (交易者明确该做什么)
        if cur_label == '冰点':
            lines.append(f'⚠️ {cur_label}情绪: 管住手, 空仓观望')
        elif cur_label == '中性' and cur['score'] < 45:
            lines.append('⚠️ 中性偏弱: 轻仓试错, 不追高')
        if payload.get('boards'):
            lines.append(f'▼ 领跌板块 (L2)')
            for name, zaf, state in payload['boards']:
                lines.append(f'├─ {name} {zaf:+.1f}% [{state}]')
        if payload.get('stocks'):
            lines.append(f'▼ 个股梯队异动 (L3)')
            for name, zaf, role in payload['stocks']:
                lines.append(f'├─ {name} {zaf:+.1f}% {role}')
        return self._dispatch(f'🔴 大盘跳水预警 | {now.strftime("%H:%M")}', lines, now, lane=1)

    # ⑥ 🚀 开盘拉升 (传导链①龙头异动; 事件, ≤2/min bucket; send_warn 另走客户端)
    def on_open_surge(self, code: str, name: str, price: float, rise: float,
                      now: datetime) -> bool:
        lines = [f'🚀 开盘拉升 | {now.strftime("%H:%M")}', '',
                 f'{name}({code}) {price:.2f}  相对开盘 {rise:+.1f}%']
        return self._dispatch(f'🚀 开盘拉升 {name}', lines, now, lane=1)

    # ⑦ 💥 个股炸板预警 (tail 段; 事件, ≤2/min)
    def on_blast_alert(self, tail: dict, now: datetime) -> bool:
        lines = [f'💥 尾盘炸板预警 | {now.strftime("%H:%M")}', '',
                 f'封板候选 {tail.get("sealed_n", 0)} 只, 炸板 {tail.get("blast_n", 0)} 只',
                 tail.get('blast_s', '-')]
        return self._dispatch(f'💥 尾盘炸板 | {now.strftime("%H:%M")}', lines, now, lane=1)

    # 💥 个股炸板实时 (blindspot 盲区 6s 粒度; FCAmo 封→开瞬间; 盘中/tail)
    def on_seal_break(self, code: str, name: str, prev: float,
                      break_n: int = 0, ever_zt: int = 0, boards: list | None = None,
                      zaf: float = 0, fhsl: float = 0,
                      now: datetime | None = None) -> bool:
        lines = [f'💥 炸板 | {now.strftime("%H:%M")}', '']
        lb = f'  {int(ever_zt)}连板' if ever_zt >= 2 else ''
        hb = '  [高标]' if ever_zt >= 3 else ''
        zaf_s = f'  {zaf:+.1f}%' if zaf else ''
        hsl_s = f'  [换手{fhsl:.0f}%]' if fhsl > 8 else ''
        lines.append(f'{name}{lb}{hb}{zaf_s}  封单 {prev:.0f}万→0'
                     + (f'  [今日炸板{break_n}次]' if break_n else ''))
        if boards:
            lines.append(f'  [{" ".join(boards)}]')
        return self._dispatch(f'💥 炸板 {name}', lines, now, lane=1)

    # 🔁 炸板回封实时 (blindspot 盲区 6s 粒度; FCAmo 开→封瞬间)
    def on_seal_back(self, code: str, name: str, cur: float,
                     back_n: int = 0, ever_zt: int = 0, boards: list | None = None,
                     zaf: float = 0, fhsl: float = 0,
                     now: datetime | None = None) -> bool:
        lines = [f'🔁 回封 | {now.strftime("%H:%M")}', '']
        lb = f'  {int(ever_zt)}连板' if ever_zt >= 2 else ''
        zaf_s = f'  {zaf:+.1f}%' if zaf else ''
        lines.append(f'{name}{lb}{zaf_s}  回封 {cur:.0f}万'
                     + (f'  [今日回封{back_n}次]' if back_n else ''))
        if boards:
            lines.append(f'  [{" ".join(boards)}]')
        return self._dispatch(f'🔁 回封 {name}', lines, now, lane=1)

    # ⚠️ 封单衰竭前兆 (blindspot 6s 粒度; FCAmo 连续2轮降≥40% 仍封, 炸板前兆)
    def on_seal_fade(self, code: str, name: str, prev: float, cur: float,
                     ever_zt: int = 0, boards: list | None = None,
                     zaf: float = 0, fhsl: float = 0,
                     now: datetime | None = None) -> bool:
        lines = [f'⚠️ 封单衰竭 | {now.strftime("%H:%M")}', '']
        lb = f'  {int(ever_zt)}连板' if ever_zt >= 2 else ''
        hb = '  [高标]' if ever_zt >= 3 else ''
        zaf_s = f'  {zaf:+.1f}%' if zaf else ''
        lines.append(f'{name}{lb}{hb}{zaf_s}  封单 {prev:.0f}万→{cur:.0f}万 (缩{f"{(1-cur/prev)*100:.0f}"}%)')
        if boards:
            lines.append(f'  [{" ".join(boards)}]')
        lines.append(f'  ⚠️ 连续2轮缩量, 有炸板风险, 注意减仓')
        return self._dispatch(f'⚠️ 封单衰竭 {name}', lines, now, lane=1)

    # ============ 机会类事件 (盘中决策最缺; 与负向共用 ≤2/min bucket) ============

    # 🟢 板块新主线入池 (状态机 NEW; 60s内发现新方向, 系统最大价值)
    def on_board_new(self, boards: list, now: datetime) -> bool:
        lines = [f'🟢 新主线 | {now.strftime("%H:%M")}']
        for b in boards:
            lines.append(f'{b["name"]}  涨幅{b["zaf"]:+.1f}%  涨停{b["zt"]}')
            if b.get('lights'):
                lines.append(f'  [探照灯: {" ".join(b["lights"])}]')
        return self._dispatch(f'🟢 新主线 {len(boards)} 板块', lines, now, lane=0)

    # 🔔 竞价定调 (9:25 竞价结束一次; 汇总通道不占 2/min 配额)
    def on_auction_preview(self, lines: list, now: datetime) -> bool:
        ok = _send('🔔 竞价定调', lines, self.dry_run)
        if ok:
            self.send_ok += 1
        else:
            self.send_fail += 1
        return ok

    # 🚀 龙头涨停预测 (9:25 竞价结束一次; 汇总通道不占 2/min 配额)
    def on_zt_forecast(self, lines: list, now: datetime) -> bool:
        ok = _send('🚀 龙头涨停预测', lines, self.dry_run)
        if ok:
            self.send_ok += 1
        else:
            self.send_fail += 1
        return ok

    # 🔥 板块趋势确认 (连续 N 轮 HOT, 非单轮脉冲)
    def on_hot_streak(self, board: dict, now: datetime) -> bool:
        lines = [f'🔥 趋势确认 | {now.strftime("%H:%M")}', '',
                 f'{board["name"]}  连续{board["rounds"]}轮HOT  动能分{board["score"]:.0f}',
                 f'涨停 {board["zt_prev"]}→{board["zt_cur"]}']
        return self._dispatch(f'🔥 趋势确认 {board["name"]}', lines, now, lane=0)

    # 🚀 龙头封板 (drilled 涨停股; 封单/封成比/首次封板时间)
    def on_limit_up(self, stocks: list, now: datetime) -> bool:
        lines = [f'🚀 龙头封板 | {now.strftime("%H:%M")}']
        for s in stocks:
            name = s['name']
            # 连板标记: ≥2 板显眼
            lb = s.get('ever_zt', 0)
            lb_s = f'  {int(lb)}连板' if lb >= 2 else ''
            # 烂板标注: 封成比<0.05 或 炸板≥2 = 烂板 (不是硬板别追)
            is_weak = s.get('fcb', 0) < 0.05 or s.get('break_n', 0) >= 2
            weak_s = '  [烂板]' if is_weak else ''
            # 换手: <3% 疑似一字(买不进), >8% 换手板(可排板)
            hsl = s.get('fHSL', 0)
            hsl_s = '  [一字]' if 0 < hsl < 3 else ('  [换手]' if hsl > 8 else '')
            # 位置: 高位板(接近52周高)风险高
            pos = s.get('pos_ratio', 0)
            pos_s = '  [高位]' if pos > 0.9 else ''
            # 主力: 封板后主力流入/流出
            zjl = s.get('zjl_hb', 0)
            zjl_s = f'  主力{int(zjl/1e4)}亿' if abs(zjl) > 0 else ''
            lines.append(f'{name}{lb_s}  {s["zaf"]:+.1f}%  封单{s["fcamo"]:.0f}万'
                         f' 封成比{s["fcb"]:.2f}{weak_s}{hsl_s}{pos_s}')
            if zjl_s:
                lines.append(f'  {zjl_s.strip()}')
            if s.get('first_limit'):
                lines.append(f'  ⏱ 首封 {s["first_limit"]}')
            if s.get('boards'):
                lines.append(f'  [{" ".join(s["boards"][:3])}]')
        return self._dispatch(f'🚀 龙头封板 {len(stocks)} 只', lines, now, lane=0)


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
