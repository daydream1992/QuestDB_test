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


def _send(payload, chat_id=None):
    """统一发送: Webhook 优先, 降级 API。

    Args:
        payload: Webhook 消息体 (msg_type + content/card)
        chat_id: 可选, 指定群聊 ID (API 通道用)
    Returns:
        bool: 是否成功
    """
    # Dry-Run 模式 (CLAUDE.md §9: 调试即污染生产群)
    if _cfg.DRY_RUN:
        logger.info('[DRY-RUN] 拦截推送: {}', payload.get('msg_type'))
        _stats_inc('dry_run')
        return True
    # 全局频控
    if not _global_rate_allow():
        logger.warning('全局频控拦截 (≤{}条/分钟)', _GLOBAL_MAX_PER_MIN)
        _stats_inc('rate_limited')
        return False

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
    return _send(
        {'msg_type': 'text', 'content': {'text': text}},
        chat_id=chat_id,
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

    # 频控检查 + 日志在进程内锁保护 (避免 _allow_push 与 _log_freq 之间插入)
    from lib.qdb import connect
    con = connect()
    try:
        with _freq_lock:
            allowed = _allow_push(con, code, signal_type)
            if not allowed:
                logger.info('信号频控拦截: %s|%s', code, signal_type)
                return False

            card = _build_signal_card(signal)
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
    from lib.qdb import connect
    con = connect()
    try:
        with _freq_lock:
            from lib.qdb import query_one
            key = f'dec_{code}|{action}'
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


# ══════════════════════════════════════════════════════════
# 卡片构造
# ══════════════════════════════════════════════════════════

def _build_signal_card(signal):
    """构造信号卡片"""
    stype = signal.get('signal_type', '')
    color = _COLOR_MAP.get(stype, 'blue')
    title = f'信号 · {stype or "unknown"}'

    lines = [
        f'**代码**: {signal.get("code", "")}',
        f'**股票名称**: {signal.get("stock_name", "")}',
        f'**策略**: {signal.get("strategy_name", "")}',
        f'**类型**: {stype}',
        f'**评分**: {signal.get("signal_score", "")}',
        f'**价格**: {signal.get("price", "")}',
        f'**成交量**: {signal.get("volume", "")}',
        f'**时间**: {signal.get("signal_time", "")}',
        f'**原因**: {signal.get("reason", "")}',
    ]
    md = '\n'.join(lines)
    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': title},
            'template': color,
        },
        'elements': [
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': md}},
        ],
    }


def _build_decision_card(decision):
    """构造决策卡片"""
    action = decision.get('action', '')
    color = _COLOR_MAP.get(action, 'blue')
    title = f'决策 · {action or "unknown"}'

    lines = [
        f'**代码**: {decision.get("code", "")}',
        f'**股票名称**: {decision.get("stock_name", "")}',
        f'**策略**: {decision.get("strategy_name", "")}',
        f'**动作**: {action}',
        f'**建议仓位**: {decision.get("position_size", "")}%',
        f'**价格**: {decision.get("price", "")}',
        f'**时间**: {decision.get("decision_time", "")}',
        f'**原因**: {decision.get("reason", "")}',
    ]
    md = '\n'.join(lines)
    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': title},
            'template': color,
        },
        'elements': [
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': md}},
        ],
    }
