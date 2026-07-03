"""飞书推送

脚本路径: K:\QuestDB_test\\lib\\lark.py
用途: 飞书 webhook 推送纯文本 / 信号 / 决策, 含频控
依赖: requests, psycopg2, python-dotenv, lib.qdb
数据源: -
入库表: qd_signal_log (信号推送频控日志)
说明:
  - WEBHOOK 从 config/.env 的 LARK_WEBHOOK_URL 读取
  - 频控: 同 code+signal_type 5 分钟内只推一次
  - 频控查询/写入 qd_signal_log 表
  - 注意: qd_signal_log 表 strategy_name 字段复用为频控键 "code|signal_type"
    (该表 DEDUP KEYS 为 (log_time, strategy_name), 无独立 code/signal_type 字段,
     此处以 strategy_name 存频控键, 不改 DDL)
"""

import os
import json
import logging

import requests
from dotenv import load_dotenv

# 加载 config/.env
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', '.env')
load_dotenv(_ENV_PATH)

WEBHOOK = os.getenv('LARK_WEBHOOK_URL', '')

# 频控窗口 (秒): 同 code+signal_type 5 分钟内只推一次
COOLDOWN_SEC = 300

logger = logging.getLogger(__name__)

# signal_type / action → 卡片颜色
_COLOR_MAP = {
    'buy': 'green',
    'sell': 'red',
    'warn': 'orange',
    'observe': 'blue',
    'hold': 'grey',
    'stop_loss': 'red',
    'stop_profit': 'green',
}


def _freq_key(code, signal_type):
    """生成频控键 "code|signal_type" """
    return '{c}|{s}'.format(c=code or '', s=signal_type or '')


def _allow_push(con, code, signal_type):
    """频控判断: 同 code+signal_type 在 COOLDOWN_SEC 内是否已推过

    查 qd_signal_log 表, strategy_name 字段存频控键。

    Returns:
        bool: True 表示允许推送
    """
    from lib.qdb import query_one
    key = _freq_key(code, signal_type)
    row = query_one(
        con,
        "SELECT last_push_time FROM qd_signal_log "
        "WHERE strategy_name = %s ORDER BY log_time DESC LIMIT 1",
        (key,))
    if row is None:
        return True
    last = row.get('last_push_time')
    if last is None:
        return True
    # psycopg2 对 TIMESTAMP 返回 datetime; 兼容字符串
    from datetime import datetime
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last.replace('Z', ''))
        except Exception:
            return True
    delta = (datetime.now() - last).total_seconds()
    return delta >= COOLDOWN_SEC


def _log_freq(con, code, signal_type, pushed):
    """记录频控日志到 qd_signal_log

    Args:
        con: psycopg2 连接 (autocommit)
        code: 标的代码
        signal_type: 信号类型
        pushed: 本次是否实际推送
    """
    from datetime import datetime
    now = datetime.now()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO qd_signal_log "
            "(log_time, strategy_name, signal_count, last_push_time, cooldown_sec, pushed) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (now, _freq_key(code, signal_type), 1,
             now if pushed else None, COOLDOWN_SEC, pushed))
    finally:
        cur.close()


def _post(payload):
    """发送 webhook 请求

    Args:
        payload: dict, 飞书消息体
    Returns:
        bool: 是否成功 (HTTP 200 且飞书返回 code==0)
    """
    if not WEBHOOK:
        logger.warning('LARK_WEBHOOK_URL 未配置, 跳过推送')
        return False
    try:
        resp = requests.post(WEBHOOK, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error('飞书推送 HTTP %s: %s', resp.status_code, resp.text)
            return False
        data = resp.json()
        if data.get('code', 0) != 0:
            logger.error('飞书推送业务错误: %s', data)
            return False
        return True
    except Exception as e:
        logger.exception('飞书推送异常: %s', e)
        return False


def push_text(text):
    """推送纯文本到飞书

    Args:
        text: 文本内容
    Returns:
        bool: 是否成功
    """
    return _post({'msg_type': 'text', 'content': {'text': text}})


def _send_card(card):
    """发送交互卡片"""
    return _post({'msg_type': 'interactive', 'card': card})


def push_signal(signal):
    """推送信号 (格式化卡片, 含频控)

    频控: 同 code+signal_type 5 分钟内只推一次 (查 qd_signal_log)。

    Args:
        signal: dict, 字段对齐 qd_signals 表
            {code, signal_time, strategy_name, signal_type,
             signal_score, price, volume, reason, metadata}

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
    ok = _send_card(card)

    # 记录频控日志
    con = connect()
    try:
        _log_freq(con, code, signal_type, ok)
    finally:
        con.close()
    return ok


def push_decision(decision):
    """推送策略决策 (格式化卡片)

    Args:
        decision: dict, 字段对齐 qd_decisions 表
            {decision_time, code, strategy_name, action,
             position_size, price, reason}

    Returns:
        bool: 是否成功
    """
    card = _build_decision_card(decision)
    return _send_card(card)


def _build_signal_card(signal):
    """构造信号卡片"""
    stype = signal.get('signal_type', '')
    color = _COLOR_MAP.get(stype, 'blue')
    title = '信号 · {t}'.format(t=stype or 'unknown')

    lines = [
        '**代码**: {c}'.format(c=signal.get('code', '')),
        '**策略**: {s}'.format(s=signal.get('strategy_name', '')),
        '**类型**: {t}'.format(t=stype),
        '**评分**: {v}'.format(v=signal.get('signal_score', '')),
        '**价格**: {p}'.format(p=signal.get('price', '')),
        '**成交量**: {v}'.format(v=signal.get('volume', '')),
        '**时间**: {t}'.format(t=signal.get('signal_time', '')),
        '**原因**: {r}'.format(r=signal.get('reason', '')),
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
    title = '决策 · {a}'.format(a=action or 'unknown')

    lines = [
        '**代码**: {c}'.format(c=decision.get('code', '')),
        '**策略**: {s}'.format(s=decision.get('strategy_name', '')),
        '**动作**: {a}'.format(a=action),
        '**建议仓位**: {p}%'.format(p=decision.get('position_size', '')),
        '**价格**: {p}'.format(p=decision.get('price', '')),
        '**时间**: {t}'.format(t=decision.get('decision_time', '')),
        '**原因**: {r}'.format(r=decision.get('reason', '')),
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
