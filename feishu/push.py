"""飞书推送模块 (Webhook + API 双通道)

功能:
  - push_text:     纯文本推送
  - push_signal:   信号卡片推送 (含频控)
  - push_decision: 决策卡片推送
  - send_to_chat:  通过 API 发消息到指定群/人

通道策略: Webhook 优先 (零依赖), Webhook 不可用时降级到 API。
频控: 同 code+signal_type 在 SIGNAL_COOLDOWN_SEC 内只推一次 (查 qd_signal_log)。
     全局 ≤ _GLOBAL_MAX_PER_MIN 条/分钟 (进程内令牌桶)。
"""

import os
import sys
import time
import logging
import threading
from collections import deque
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('feishu.config')
_auth = importlib.import_module('feishu.auth')

# 使 qdb 可用
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

logger = logging.getLogger(__name__)

# 模块级 requests Session (复用 TCP+TLS, 避免每次新建)
_session = requests.Session()

# ── 推送监控统计 (进程内) ────────────────────────────────────

PUSH_STATS = {
    'sent_total': 0,
    'webhook_ok': 0,
    'api_ok': 0,
    'failed': 0,
    'rate_limited': 0,
    'dry_run': 0,
}
_stats_lock = threading.Lock()


def _stats_inc(key):
    with _stats_lock:
        PUSH_STATS[key] = PUSH_STATS.get(key, 0) + 1


def get_push_stats():
    """返回推状态统计快照 (线程安全)"""
    with _stats_lock:
        return dict(PUSH_STATS)


def reset_push_stats():
    """重围推送统计"""
    with _stats_lock:
        for k in PUSH_STATS:
            PUSH_STATS[k] = 0

# ── 全局频控 (CLAUDE.md §9: ≤ 2 条/分钟) ────────────────────

_global_lock = threading.Lock()
_global_history = []  # list[float] — 近 60s 内推送时间戳
_GLOBAL_MAX_PER_MIN = 2


def _global_rate_allow() -> bool:
    """全局令牌桶: 每分钟最多 _GLOBAL_MAX_PER_MIN 条。

    Returns:
        bool: True = 允许推送
    """
    with _global_lock:
        now = time.time()
        # 清理 60s 外的旧记录
        _global_history[:] = [t for t in _global_history if now - t < 60]
        if len(_global_history) >= _GLOBAL_MAX_PER_MIN:
            return False
        _global_history.append(now)
        return True

_TEXT_LOCK = threading.Lock()
_TEXT_HISTORY: list[float] = []
_TEXT_MAX_PER_MIN = 10


def _text_rate_allow() -> bool:
    """文字推送令牌桶: 每分钟最多 _TEXT_MAX_PER_MIN 条。

    Returns:
        bool: True = 允许推送
    """
    with _TEXT_LOCK:
        now = time.time()
        _TEXT_HISTORY[:] = [t for t in _TEXT_HISTORY if now - t < 60]
        if len(_TEXT_HISTORY) >= _TEXT_MAX_PER_MIN:
            return False
        _TEXT_HISTORY.append(now)
        return True


# ── 卡片颜色映射 ──────────────────────────────────────────
_COLOR_MAP = {
    'buy': 'green',
    'sell': 'red',
    'warn': 'orange',
    'observe': 'blue',
    'hold': 'grey',
    'stop_loss': 'red',
    'stop_profit': 'green',
    'surge_up': 'green',
    'surge_down': 'red',
    'limit_seal': 'green',
    'limit_break': 'red',
    'capital_in': 'green',
    'capital_out': 'red',
}


# ── T1 卡片辅助函数 ────────────────────────────────────────


def _get_price_color(stype, reason):
    """根据信号类型/动作推断价格颜色"""
    green_types = {'buy', 'stop_profit', 'surge_up', 'limit_seal', 'capital_in'}
    red_types = {'sell', 'stop_loss', 'surge_down', 'limit_break', 'capital_out'}
    # 决策 action 映射
    if stype in green_types:
        return 'green'
    if stype in red_types:
        return 'red'
    return 'grey'


def _price_change_md(stype, reason):
    """生成涨跌幅描述 (从 reason 中提取正负号)"""
    if stype in ('limit_seal', 'surge_up'):
        return '<font color="green">+涨停</font>'
    if stype in ('limit_break', 'surge_down'):
        return '<font color="red">-跌停</font>'
    # 尝试从 reason 提取价格变动
    if reason:
        import re as _re
        m = _re.search(r'[+-]\d+\.?\d*%', reason)
        if m:
            v = float(m.group().rstrip('%'))
            c = 'green' if v > 0 else 'red' if v < 0 else 'grey'
            return f'<font color="{c}">{m.group()}</font>'
    return ''


def _score_bar(score, total=10):
    """生成 Unicode 进度条: ████████░░"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return '░' * total
    filled = int(round(s / 100 * total))
    filled = max(0, min(total, filled))
    return '█' * filled + '░' * (total - filled)


# ══════════════════════════════════════════════════════════
# 频控 (复用 qd_signal_log 表, 逻辑同旧 lib/lark.py)
# ══════════════════════════════════════════════════════════

_freq_lock = threading.Lock()


def _freq_key(code, signal_type):
    """频控键 "code|signal_type" """
    return f'{code or ""}|{signal_type or ""}'


def _allow_push(con, code, signal_type):
    """频控判断: 同 code+signal_type 在 COOLDOWN 内是否已推过。

    查 qd_signal_log 表, strategy_name 字段存频控键。

    Returns:
        bool: True = 允许推送
    """
    from lib.qdb import query_one
    key = _freq_key(code, signal_type)
    row = query_one(
        con,
        "SELECT last_push_time FROM qd_signal_log "
        "WHERE strategy_name = %s ORDER BY log_time DESC LIMIT 1",
        (key,),
    )
    if row is None:
        return True
    last = row.get('last_push_time')
    if last is None:
        return True
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last.replace('Z', ''))
        except Exception:
            return True
    delta = (datetime.now() - last).total_seconds()
    return delta >= _cfg.SIGNAL_COOLDOWN_SEC


def _log_freq(con, code, signal_type, pushed):
    """记录频控日志到 qd_signal_log"""
    now = datetime.now()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO qd_signal_log "
            "(log_time, strategy_name, signal_count, last_push_time, cooldown_sec, pushed) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (now, _freq_key(code, signal_type), 1,
             now if pushed else None, _cfg.SIGNAL_COOLDOWN_SEC, pushed),
        )
    finally:
        cur.close()


# ══════════════════════════════════════════════════════════
# 底层发送
# ══════════════════════════════════════════════════════════

def _post_webhook(payload, _retries=3):
    """通过 Webhook 发送消息 (指数退避重试)。

    Args:
        payload: 消息体
        _retries: 内部参数, 剩余重试次数

    Returns:
        bool: 是否成功 (HTTP 200 且飞书返回 code==0)
    """
    if not _cfg.WEBHOOK_URL:
        logger.debug('LARK_WEBHOOK_URL 未配置, 跳过 Webhook 推送')
        return False
    last_err = None
    for attempt in range(1, _retries + 1):
        try:
            resp = _session.post(_cfg.WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code != 200:
                if attempt < _retries:
                    time.sleep(2 ** (attempt - 1))
                    continue
                logger.error('Webhook 推送 HTTP %s: %s', resp.status_code, resp.text)
                return False
            data = resp.json()
            if data.get('code', 0) != 0:
                logger.error('Webhook 业务错误: %s', data)
                return False
            return True
        except Exception as e:
            last_err = e
            if attempt < _retries:
                time.sleep(2 ** (attempt - 1))
                continue
    logger.warning('Webhook 推送异常(重试耗尽): %s', last_err)
    return False


_INVALID_TOKEN_CODES = {99991663, 99991664, 99991668, 99991671}


def _post_api(chat_id, msg_type, content, _retry=True, _retries=3):
    """通过飞书 API 发消息到指定群/人 (指数退避重试)。

    Args:
        chat_id: 群聊 ID 或 user_id (open_id)
        msg_type: 消息类型 (text / interactive / post 等)
        content: 消息内容 dict
        _retry: 内部参数, token 失效重试的标记
        _retries: 网络异常时重试次数

    Returns:
        bool: 是否成功
    """
    headers = _auth.auth_headers()
    if not headers:
        return False
    body = {
        'receive_id': chat_id,
        'msg_type': msg_type,
        'content': content,
    }
    last_err = None
    for attempt in range(1, _retries + 1):
        try:
            resp = _session.post(
                f'{_cfg.BASE_URL}/im/v1/messages?receive_id_type=chat_id',
                headers=headers,
                json=body,
                timeout=10,
            )
            data = resp.json()
            code = data.get('code', -1)
            if code in _INVALID_TOKEN_CODES and _retry:
                logger.warning('token 失效 (code=%s), 刷新后重试', code)
                _auth.invalidate_token()
                return _post_api(chat_id, msg_type, content, _retry=False)
            if code != 0:
                logger.error('API 推送失败: %s', data)
                return False
            return True
        except Exception as e:
            last_err = e
            if attempt < _retries:
                time.sleep(2 ** (attempt - 1))
                continue
    logger.warning('API 推送异常(重试耗尽): %s', last_err)
    return False


def _send(payload, chat_id=None, critical=False):
    """统一发送: Webhook 优先, 降级 API。

    Args:
        payload: Webhook 消息体 (msg_type + content/card)
        chat_id: 可选, 指定群聊 ID (API 通道用)
        critical: 聚合桶和 focus 池等非人类推送跳过全局频控
    Returns:
        bool: 是否成功
    """
    # Dry-Run 模式 (CLAUDE.md §9: 调试即污染生产群)
    if _cfg.DRY_RUN:
        logger.info('[DRY-RUN] 拦截推送: {}', payload.get('msg_type'))
        _stats_inc('dry_run')
        return True
    # 全局频控 (进程内滑动窗口)
    if not critical and not _global_rate_allow():
        logger.warning('全局频控拦截 (≤{}条/分钟)', _GLOBAL_MAX_PER_MIN)
        _stats_inc('rate_limited')
        return False
    # 跨进程频控: 查 qd_signal_log 最近 60s 推送数 (多进程合计 ≤2条/分钟)
    try:
        from lib.qdb import connect, query_one, cutoff
        _qcon = connect()
        try:
            row = query_one(_qcon,
                "SELECT COUNT(*) as cnt FROM qd_signal_log "
                f"WHERE log_time > '{cutoff(minutes=1)}' AND pushed = TRUE")
            if row and int(row.get('cnt', 0)) >= _GLOBAL_MAX_PER_MIN:
                logger.warning('跨进程频控拦截 (已有%d条/分钟)', int(row.get('cnt', 0)))
                _stats_inc('rate_limited')
                return False
        finally:
            _qcon.close()
    except Exception:
        pass  # DB 不可用时仅依赖进程内频控

    # 1) Webhook 优先
    if _post_webhook(payload):
        _stats_inc('sent_total')
        _stats_inc('webhook_ok')
        return True
    # 2) 降级 API (需要 chat_id)
    if chat_id and _cfg.has_app_credentials():
        msg_type = payload.get('msg_type', 'text')
        if msg_type == 'interactive':
            content = {'config': payload.get('card', {})}
            ok = _post_api(chat_id, 'interactive', content)
        else:
            ok = _post_api(chat_id, msg_type, payload.get('content', {}))
        if ok:
            _stats_inc('sent_total')
            _stats_inc('api_ok')
        else:
            _stats_inc('failed')
        return ok
    _stats_inc('failed')
    return False


# ══════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════

def push_text(text, chat_id=None):
    """推送纯文本到飞书。

    Args:
        text: 文本内容
        chat_id: 可选群聊 ID (API 降级用)
    Returns:
        bool: 是否成功
    """
    if not _text_rate_allow():
        logger.warning('push_text 频控拦截 (≤{}条/分钟)', _TEXT_MAX_PER_MIN)
        _stats_inc('rate_limited')
        return False
    return _send(
        {'msg_type': 'text', 'content': {'text': text}},
        chat_id=chat_id, critical=False
    )


def push_signal(signal, chat_id=None):
    """推送信号 (格式化卡片, 含频控)。

    频控: 同 code+signal_type 在 SIGNAL_COOLDOWN_SEC 内只推一次。

    Args:
        signal: dict, 字段对齐 qd_signals 表
            {code, signal_time, strategy_name, signal_type,
             signal_score, price, volume, reason, metadata}
        chat_id: 可选群聊 ID

    Returns:
        bool: 是否实际推送 (频控拦截返回 False)
    """
    code = signal.get('code', '')
    signal_type = signal.get('signal_type', '')

    # 频控检查 (DB 查询) 在锁外, 避免锁住 I/O
    from lib.qdb import connect
    con = connect()
    try:
        allowed = _allow_push(con, code, signal_type)
        if not allowed:
            logger.info('信号频控拦截: %s|%s', code, signal_type)
            return False

        card = _build_signal_card(signal)

        # 锁只保护发送 + 频控日志写入 (原子 check-and-log)
        with _freq_lock:
            ok = _send(
                {'msg_type': 'interactive', 'card': card},
                chat_id=chat_id,
            )
            _log_freq(con, code, signal_type, ok)
    finally:
        con.close()
    return ok


def push_decision(decision, chat_id=None):
    """推送策略决策 (格式化卡片, 含单标的频控)。

    Args:
        decision: dict, 字段对齐 qd_decisions 表
            {decision_time, code, strategy_name, action,
             position_size, price, reason}
        chat_id: 可选群聊 ID

    Returns:
        bool: 是否成功
    """
    # 单标的大类频控 (复用信号频控逻辑, code+action 每 60s 1 次)
    code = decision.get('code', '')
    action = decision.get('action', '')
    key = f'dec_{code}|{action}'

    # DB 查询在锁外进行
    from lib.qdb import connect, query_one
    con = connect()
    try:
        row = query_one(
            con,
            "SELECT last_push_time FROM qd_signal_log "
            "WHERE strategy_name = %s ORDER BY log_time DESC LIMIT 1",
            (key,),
        )
        if row:
            last = row.get('last_push_time')
            if last:
                if isinstance(last, str):
                    try:
                        last = datetime.fromisoformat(last.replace('Z', ''))
                    except Exception:
                        last = None
                if last and (datetime.now() - last).total_seconds() < 60:
                    logger.info('决策频控拦截: %s|%s', code, action)
                    return False
        card = _build_decision_card(decision)

        # 锁只保护发送 + 频控日志写入 (原子 check-and-log)
        with _freq_lock:
            ok = _send(
                {'msg_type': 'interactive', 'card': card},
                chat_id=chat_id,
            )
            # 记频控日志
            now = datetime.now()
            cur = con.cursor()
            try:
                cur.execute(
                    "INSERT INTO qd_signal_log "
                    "(log_time, strategy_name, signal_count, last_push_time, cooldown_sec, pushed) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (now, key, 1, now if ok else None, 60, ok),
                )
            finally:
                cur.close()
            return ok
    finally:
        con.close()


def send_to_chat(chat_id, msg_type, content):
    """通过 API 发消息到指定群/人 (仅 API 通道)。

    Args:
        chat_id: 群聊 ID 或 open_id
        msg_type: 消息类型 (text / post / interactive 等)
        content: 消息内容 dict (按飞书消息体格式)
    Returns:
        bool: 是否成功
    """
    return _post_api(chat_id, msg_type, content)


def push_focus_pool(pool_df, chat_id=None):
    """推送 focus 池到飞书 (表格卡片)。

    Args:
        pool_df: DataFrame, 包含 code/Now/Volume/LastClose 等字段
        chat_id: 可选群聊 ID

    Returns:
        bool: 是否成功
    """
    if pool_df is None or pool_df.empty:
        return False

    rows = []
    for _, r in pool_df.iterrows():
        code = r.get('code', '')
        name = r.get('name', r.get('stock_name', ''))
        # change_pct 计算: 如果传入的只有 pricevol 表 (只有 Now/LastClose)
        now = r.get('Now', 0)
        lc = r.get('LastClose', 0)
        try:
            now_v = float(now)
            lc_v = float(lc)
            chg = ((now_v - lc_v) / lc_v * 100) if lc_v > 0 else 0.0
        except (TypeError, ValueError):
            chg = 0.0
        volume = r.get('Volume', 0)
        color = 'green' if chg > 0 else 'red' if chg < 0 else 'grey'
        change_md = f'<font color="{color}">{chg:+.2f}%</font>'

        rows.append([
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'**{code}**'}},
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': name[:8]}},
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': change_md}},
        ])

    card = {
        'header': {
            'title': {'tag': 'lark_md', 'content': f'📊 Focus 池 ({len(rows)} 只)'},
            'template': 'blue',
        },
        'elements': [
            {
                'tag': 'table',
                'columns': [
                    {'tag': 'div', 'text': {'tag': 'lark_md', 'content': '代码'}},
                    {'tag': 'div', 'text': {'tag': 'lark_md', 'content': '名称'}},
                    {'tag': 'div', 'text': {'tag': 'lark_md', 'content': '涨幅'}},
                ],
                'elements': rows[:50],
            },
        ]
    }

    return _send({'msg_type': 'interactive', 'card': card}, chat_id=chat_id, critical=True)


# ══════════════════════════════════════════════════════════
# 卡片构造
# ══════════════════════════════════════════════════════════

def _build_signal_card(signal):
    """T1 单标的精推卡片 (column_set 分栏 + 价格着色 + 评分进度条)

    结构:
      ┌──────────────────────────────────────────┐
      │ [Header 绿/红/橙] 信号·buy 09:35:12       │
      ├──────────────────────────────────────────┤
      │ ┌──────────┬─────────────────────────┐  │
      │ │ 002479   │ 富春环保                 │  │
      │ │ 5.62     │ +9.98% 涨停              │  │
      │ └──────────┴─────────────────────────┘  │
      │ ─────────────────────────               │
      │ 评分  87/100 ████████░░                 │
      │ 策略  dark_money · 大单净流入           │
      │ ─────────────────────────               │
      │ 原因  09:33 主力单笔8000手 → 09:34 炸板  │
      └──────────────────────────────────────────┘
    """
    stype = signal.get('signal_type', '')
    color = _COLOR_MAP.get(stype, 'blue')
    title = f'信号 · {stype or "unknown"}'

    code = signal.get('code', '')
    name = signal.get('stock_name', '') or ''
    price = signal.get('price', '')
    score = signal.get('signal_score', '')
    strategy = signal.get('strategy_name', '')
    reason = signal.get('reason', '') or ''

    # 价格涨跌着色 (根据 reason 或 signal_type 判断)
    price_color = _get_price_color(stype, reason)
    price_md = f'<font color="{price_color}">**{price}**</font>'

    # 评分进度条 (10 格)
    score_bar = _score_bar(score)

    elements = [
        # 1. 主体分栏: 左代码, 右名称+价格
        {
            'tag': 'column_set',
            'flex_mode': 'none',
            'background_style': 'default',
            'columns': [
                {
                    'tag': 'column',
                    'width': 'weighted',
                    'weight': 1,
                    'vertical_align': 'top',
                    'elements': [
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': f'**{code}**'}},
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': price_md}},
                    ],
                },
                {
                    'tag': 'column',
                    'width': 'weighted',
                    'weight': 2,
                    'vertical_align': 'top',
                    'elements': [
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': f'{name}'}},
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': _price_change_md(stype, reason)}},
                    ],
                },
            ],
        },
        {'tag': 'hr'},
        # 2. 评分进度条
        {
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'评分  {score}/100  {score_bar}'},
        },
        # 3. 策略 + 时机
        {
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'策略  {strategy}'},
        },
        {'tag': 'hr'},
        # 4. 原因 (重点行, 用引述样式)
        {
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'原因  {reason}'},
        },
    ]

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': title},
            'template': color,
        },
        'elements': elements,
    }


def _build_decision_card(decision):
    """T1 单标的精推卡片 (决策版, 字段: action/position_size)

    结构同 _build_signal_card, 但用 action 替代 signal_type,
    position_size 替代 signal_score。
    """
    action = decision.get('action', '')
    color = _COLOR_MAP.get(action, 'blue')
    title = f'决策 · {action or "unknown"}'

    code = decision.get('code', '')
    name = decision.get('stock_name', '') or ''
    price = decision.get('price', '')
    pos = decision.get('position_size', '')
    strategy = decision.get('strategy_name', '')
    reason = decision.get('reason', '') or ''

    price_color = _get_price_color(action, reason)
    price_md = f'<font color="{price_color}">**{price}**</font>'

    elements = [
        {
            'tag': 'column_set',
            'flex_mode': 'none',
            'background_style': 'default',
            'columns': [
                {
                    'tag': 'column',
                    'width': 'weighted',
                    'weight': 1,
                    'vertical_align': 'top',
                    'elements': [
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': f'**{code}**'}},
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': price_md}},
                    ],
                },
                {
                    'tag': 'column',
                    'width': 'weighted',
                    'weight': 2,
                    'vertical_align': 'top',
                    'elements': [
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': name}},
                        {'tag': 'div', 'text': {'tag': 'lark_md',
                            'content': _price_change_md(action, reason)}},
                    ],
                },
            ],
        },
        {'tag': 'hr'},
        # 仓位 + 策略
        {
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'建议仓位  **{pos}%**  ·  策略  {strategy}'},
        },
        {'tag': 'hr'},
        # 原因
        {
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'原因  {reason}'},
        },
    ]

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': title},
            'template': color,
        },
        'elements': elements,
    }


# ══════════════════════════════════════════════════════════
# 5 分钟桶聚合推送 (T2 模板)
# ══════════════════════════════════════════════════════════
#
# 设计目的:
#   - 解决"逐条推送被全局频控吞掉"的问题
#   - 5 分钟一桶,桶满自动 flush 成 1 张聚合卡片
#   - 自然遵守 CLAUDE.md §9 全局 ≤2 条/分钟
#
# 与现有 push_decision 的关系:
#   - push_decision: 单条精推(低频,人工触发,如炸板/龙虎榜)
#   - push_decision_aggregated: 批量入桶(高频,盘中自动决策)
#   - 两者互不干扰,可共存
# ══════════════════════════════════════════════════════════

_bucket_lock = threading.Lock()
_bucket = deque()                  # 桶内决策列表
_bucket_start = None               # 当前桶起始时间戳
_BUCKET_WINDOW = 300               # 5 分钟 = 300s
_BUCKET_MAX_ITEMS = 10             # 桶满 10 条提前 flush
_BUCKET_TOP_N = 5                  # 卡片最多展示 5 条


def push_decision_aggregated(decision: dict, chat_id: str = None) -> bool:
    """决策入桶, 桶满(5min 或 10 条)时 flush 一张聚合卡片。

    用于盘中高频场景, 替代 push_decision。

    Args:
        decision: dict, 字段同 push_decision
            {decision_time, code, stock_name, strategy_name,
             action, position_size, price, reason, score}
        chat_id: 可选群聊 ID

    Returns:
        bool: True = 本次入桶触发 flush 且推送成功;
              False = 仅入桶未触发 flush, 或 flush 失败
    """
    global _bucket_start
    now = time.time()
    with _bucket_lock:
        if _bucket_start is None:
            _bucket_start = now
        _bucket.append(decision)
        # 触发条件: 满 5 分钟 OR 满 10 条
        should_flush = (
            now - _bucket_start >= _BUCKET_WINDOW
            or len(_bucket) >= _BUCKET_MAX_ITEMS
        )
        if not should_flush:
            return False
        bucket = list(_bucket)
        # 不清空, success 后锁内清空(避免 send 失败丢数据)

    success = _flush_bucket(bucket, chat_id=chat_id)
    if success:
        with _bucket_lock:
            _bucket.clear()
            _bucket_start = None
    return success


def flush_pending_bucket(chat_id: str = None) -> bool:
    """强制 flush 当前桶(收盘后或程序退出前调用)。

    避免桶内剩余决策丢失。

    Returns:
        bool: 是否成功(空桶返回 False)
    """
    with _bucket_lock:
        if not _bucket:
            return False
        bucket = list(_bucket)
        # 失败不清空
    success = _flush_bucket(bucket, chat_id=chat_id)
    if success:
        with _bucket_lock:
            _bucket.clear()
            _bucket_start = None
    return success


def _flush_bucket(bucket: list, chat_id: str = None) -> bool:
    """把桶内决策合并为一张 T2 聚合卡片推送"""
    if not bucket:
        return False
    # 排序: buy 优先, stop_loss 必入, 然后按 score 降序
    def _priority(d):
        action = d.get('action', '')
        # buy=0(最优先), stop_loss/stop_profit=1, observe=2, hold=3
        rank = {'buy': 0, 'stop_loss': 1, 'stop_profit': 1,
                'sell': 1, 'observe': 2, 'hold': 3}.get(action, 2)
        score = d.get('score', 0) or d.get('position_size', 0) or 0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0
        return (rank, -score)
    bucket.sort(key=_priority)
    top = bucket[:_BUCKET_TOP_N]

    card = _build_aggregate_card(top, total=len(bucket))
    ok = _send({'msg_type': 'interactive', 'card': card}, chat_id=chat_id, critical=True)

    # 写入频控日志 (每条决策一条, strategy_name = 'agg|action')
    if ok:
        _log_bucket_to_signal_log(bucket)
    return ok


_SIGNAL_LOG_COLS = ['log_time', 'strategy_name', 'signal_count',
                    'last_push_time', 'cooldown_sec', 'pushed']


def _log_bucket_to_signal_log(bucket: list):
    """flush 成功后，把桶内每条决策写入 qd_signal_log (用于跨进程频控)"""
    from lib.qdb import connect, executemany_batch
    now = datetime.now()
    rows = []
    for d in bucket:
        action = d.get('action', '')
        key = f'agg|{action}'
        rows.append((now, key, 1, now, _BUCKET_WINDOW, True))
    if not rows:
        return
    try:
        con = connect()
        try:
            executemany_batch(con, 'qd_signal_log', _SIGNAL_LOG_COLS, rows)
        finally:
            con.close()
    except Exception:
        pass  # 不阻断推送


def _build_aggregate_card(top: list, total: int) -> dict:
    """T2 聚合卡片: 头部 + 决策列表 + 尾部跳转

    结构:
      ┌────────────────────────────────────┐
      │ [Header 蓝] 09:35 盘中精选 · 12→5  │
      ├────────────────────────────────────┤
      │ 09:35 盘中精选 · 12 条决策入榜 5 条 │
      │ ─────────────────────────          │
      │ 🟢 002479 富春环保 5.62 评分87      │
      │    dark_money · 大单1.2亿           │
      │ 🟢 600118 中国卫星 18.3 评分81      │
      │    alpha_breakout · rank#3          │
      │ ...                                │
      │ ─────────────────────────          │
      │ 📊 完整列表见多维表格 [跳转]        │
      └────────────────────────────────────┘
    """
    now_dt = datetime.now().strftime('%H:%M')
    elements = []

    # 1. 头部摘要
    elements.append({
        'tag': 'div',
        'text': {'tag': 'lark_md',
                 'content': f'**{now_dt} 盘中精选** · {total} 条决策入榜 {len(top)} 条'}
    })
    elements.append({'tag': 'hr'})

    # 2. 决策列表 (每条 2 行: 主行 + 细节行)
    action_emoji = {
        'buy': '🟢', 'sell': '🔴', 'observe': '🟠',
        'stop_loss': '🔴', 'stop_profit': '🟢', 'hold': '⚪',
    }
    md_lines = []
    for d in top:
        emoji = action_emoji.get(d.get('action', ''), '⚪')
        code = d.get('code', '')
        name = d.get('stock_name', '') or ''
        price = d.get('price', '')
        score = d.get('score', '') or d.get('position_size', '')
        strategy = d.get('strategy_name', '')
        # reason 截断 60 字, 避免单条过长
        reason = (d.get('reason', '') or '')[:60]
        md_lines.append(
            f"{emoji} **{code}** {name}  {price}  评分{score}\n"
            f"   {strategy} · {reason}"
        )
    elements.append({
        'tag': 'div',
        'text': {'tag': 'lark_md', 'content': '\n\n'.join(md_lines)}
    })

    # 3. 尾部: 跳转多维表格链接 (含 table_id)
    try:
        if _cfg.BITABLE_TOKEN:
            from feishu.bitable_writer import get_bitable_url, auto_daily_table
            tid = auto_daily_table(_cfg.BITABLE_TOKEN)
            url = get_bitable_url(_cfg.BITABLE_TOKEN, tid)
            elements.append({'tag': 'hr'})
            elements.append({
                'tag': 'div',
                'text': {'tag': 'lark_md',
                         'content': f'📊 [完整列表见多维表格]({url})'}
            })
    except Exception:
        pass

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': f'盘中精选 · {now_dt}'},
            'template': 'blue',
        },
        'elements': elements,
    }


# ══════════════════════════════════════════════════════════
# T4 收盘复盘卡片
# ══════════════════════════════════════════════════════════

def build_review_card(sections: dict, k4_data: dict = None) -> dict:
    """T4 收盘复盘卡片 (4 块分区: 情绪/数据/策略/异常 + 可选 k4 板块热力/打板梯队)

    Args:
        sections: dict, 4 个分区的 Markdown 内容
            {
                'emotion':  '── 情绪变化 ──\\n  开盘: ...\\n  收盘: ...',
                'data':     '── 数据入库 ──\\n  快照: 12345 行\\n  ...',
                'strategy': '── 策略产出 ──\\n  dark_money: buy × 12\\n  ...',
                'alert':    '── 异常告警 ──\\n  日志错误: 0 ERROR',
            }
        k4_data: dict, 可选 k4 深度数据
            {
                'sentiment': '┄ PG指数: 52 中性 ┄\\n  4大指数: ...',
                'heatmap': '┄ 最强板块 ┄\\n  行业一级: 银行 +2.3%...',
                'ladder': '┄ 打板梯队 ┄\\n  首板: 23家 2板: 8家...',
            }

    Returns:
        dict: 飞书卡片 dict, 调用方用 _send({'msg_type':'interactive','card':card}) 推送
    """
    elements = []
    section_emoji = {
        'emotion': '📊',
        'data': '💾',
        'strategy': '🎯',
        'alert': '⚠️',
        'sentiment': '🧠',
        'heatmap': '🔥',
        'ladder': '🪜',
    }
    # 基础 4 分区
    for key in ('emotion', 'data', 'strategy', 'alert'):
        content = sections.get(key, '')
        if not content:
            continue
        emoji = section_emoji.get(key, '·')
        # 标题行
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md',
                'content': f'{emoji} {content.split(chr(10))[0]}'},
        })
        # 内容行 (跳过第一行标题)
        rest = '\n'.join(content.split(chr(10))[1:])
        if rest.strip():
            elements.append({
                'tag': 'div',
                'text': {'tag': 'lark_md', 'content': rest},
            })
        elements.append({'tag': 'hr'})

    # k4 扩展分区
    if k4_data:
        for key in ('sentiment', 'heatmap', 'ladder'):
            content = k4_data.get(key, '')
            if not content:
                continue
            emoji = section_emoji.get(key, '🧠')
            lines = content.strip().split(chr(10))
            elements.append({
                'tag': 'div',
                'text': {'tag': 'lark_md', 'content': f'{emoji} {lines[0]}'},
            })
            rest = '\n'.join(lines[1:])
            if rest.strip():
                elements.append({
                    'tag': 'div',
                    'text': {'tag': 'lark_md', 'content': rest},
                })
            elements.append({'tag': 'hr'})

    # 去掉末尾多余的 hr
    if elements and elements[-1].get('tag') == 'hr':
        elements.pop()

    today = datetime.now().strftime('%Y-%m-%d')
    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': f'收盘复盘 · {today}'},
            'template': 'turquoise',
        },
        'elements': elements,
    }
