"""飞书推送模块 (Webhook + API 双通道)

功能:
  - push_text:     纯文本推送
  - push_signal:   信号卡片推送 (含频控)
  - push_decision: 决策卡片推送
  - send_to_chat:  通过 API 发消息到指定群/人

通道策略: Webhook 优先 (零依赖), Webhook 不可用时降级到 API。
频控: 同 code+signal_type 在 SIGNAL_COOLDOWN_SEC 内只推一次 (查 qd_signal_log)。
"""

import os
import sys
import logging
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

def _post_webhook(payload):
    """通过 Webhook 发送消息。

    Returns:
        bool: 是否成功 (HTTP 200 且飞书返回 code==0)
    """
    if not _cfg.WEBHOOK_URL:
        logger.debug('LARK_WEBHOOK_URL 未配置, 跳过 Webhook 推送')
        return False
    try:
        resp = requests.post(_cfg.WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error('Webhook 推送 HTTP %s: %s', resp.status_code, resp.text)
            return False
        data = resp.json()
        if data.get('code', 0) != 0:
            logger.error('Webhook 业务错误: %s', data)
            return False
        return True
    except Exception as e:
        logger.warning('Webhook 推送异常: %s', e)
        return False


def _post_api(chat_id, msg_type, content):
    """通过飞书 API 发消息到指定群/人。

    Args:
        chat_id: 群聊 ID 或 user_id (open_id)
        msg_type: 消息类型 (text / interactive / post 等)
        content: 消息内容 dict

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
    try:
        resp = requests.post(
            f'{_cfg.BASE_URL}/im/v1/messages?receive_id_type=chat_id',
            headers=headers,
            json=body,
            timeout=10,
        )
        data = resp.json()
        if data.get('code', -1) != 0:
            logger.error('API 推送失败: %s', data)
            return False
        return True
    except Exception as e:
        logger.warning('API 推送异常: %s', e)
        return False


def _send(payload, chat_id=None):
    """统一发送: Webhook 优先, 降级 API。

    Args:
        payload: Webhook 消息体 (msg_type + content/card)
        chat_id: 可选, 指定群聊 ID (API 通道用)
    Returns:
        bool: 是否成功
    """
    # 1) Webhook 优先
    if _post_webhook(payload):
        return True
    # 2) 降级 API (需要 chat_id)
    if chat_id and _cfg.has_app_credentials():
        msg_type = payload.get('msg_type', 'text')
        if msg_type == 'interactive':
            content = {'config': payload.get('card', {})}
            return _post_api(chat_id, 'interactive', content)
        else:
            return _post_api(chat_id, msg_type, payload.get('content', {}))
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

    from lib.qdb import connect
    con = connect()
    try:
        allowed = _allow_push(con, code, signal_type)
    finally:
        con.close()

    if not allowed:
        logger.info('信号频控拦截: %s|%s', code, signal_type)
        return False

    card = _build_signal_card(signal)
    ok = _send(
        {'msg_type': 'interactive', 'card': card},
        chat_id=chat_id,
    )

    # 记录频控日志
    con = connect()
    try:
        _log_freq(con, code, signal_type, ok)
    finally:
        con.close()
    return ok


def push_decision(decision, chat_id=None):
    """推送策略决策 (格式化卡片)。

    Args:
        decision: dict, 字段对齐 qd_decisions 表
            {decision_time, code, strategy_name, action,
             position_size, price, reason}
        chat_id: 可选群聊 ID

    Returns:
        bool: 是否成功
    """
    card = _build_decision_card(decision)
    return _send(
        {'msg_type': 'interactive', 'card': card},
        chat_id=chat_id,
    )


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
