"""feishu.__init__: 飞书模块统一出口

脚本路径: K:\\QuestDB_test\\feishu\\__init__.py
用途: re-export 所有子模块公开函数, 提供 log_signals 一站式入口
依赖: feishu.{push,doc_writer,sheet_writer,bitable_writer,auth,config}
入参: log_signals(signals, push=False, sheet=True, bitable=True)
返回: dict {'pushed': int, 'sheet_ok': bool, 'bitable_ok': bool}
说明:
  - 飞书推送+Sheet+Bitable 三通道一次搞定
  - 各环节独立容错, 任何一个失败不影响其他
  - 2026-07-05 整理 #14: 从 4_feishu 改为 feishu, 去掉 importlib hack
  - 现在可直接 from feishu import log_signals, 但旧 importlib 风格也兼容

示例:
    from feishu import log_signals, push_text
    log_signals(signals, push=True)
    push_text('hello')
"""

import logging
import importlib as _il

# 加载子模块
_push = _il.import_module('feishu.push')
_doc = _il.import_module('feishu.doc_writer')
_sheet = _il.import_module('feishu.sheet_writer')
_bitable = _il.import_module('feishu.bitable_writer')
_auth_mod = _il.import_module('feishu.auth')
_cfg = _il.import_module('feishu.config')

_logger = logging.getLogger(__name__)

# ── push ──────────────────────────────────────────────────
push_text = _push.push_text
push_signal = _push.push_signal
push_decision = _push.push_decision
send_to_chat = _push.send_to_chat

# ── doc_writer ────────────────────────────────────────────
create_doc = _doc.create_doc
append_to_doc = _doc.append_to_doc
append_signal = _doc.append_signal
create_daily_report = _doc.create_daily_report
get_doc_url = _doc.get_doc_url

# ── sheet_writer ──────────────────────────────────────────
append_rows = _sheet.append_rows
write_signal_batch = _sheet.write_signal_batch
auto_daily_sheet = _sheet.auto_daily_sheet
ensure_headers = _sheet.ensure_headers
get_sheet_url = _sheet.get_sheet_url
SIGNAL_HEADERS = _sheet.SIGNAL_HEADERS

# ── bitable_writer ────────────────────────────────────────
create_bitable = _bitable.create_bitable
append_records = _bitable.append_records
write_signal_batch_bitable = _bitable.write_signal_batch
auto_daily_table = _bitable.auto_daily_table
get_bitable_url = _bitable.get_bitable_url

# ── auth ──────────────────────────────────────────────────
get_tenant_token = _auth_mod.get_tenant_token
auth_headers = _auth_mod.auth_headers

# ── config ────────────────────────────────────────────────
has_app_credentials = _cfg.has_app_credentials


# ══════════════════════════════════════════════════════════
# 统一入口: 一次调用完成全链路
# ══════════════════════════════════════════════════════════

def log_signals(signals, push=False, sheet=True, bitable=True):
    """信号全链路写入: 推送 + Sheet + Bitable 一次搞定。

    各环节独立容错, 任何一个失败不影响其他。

    Args:
        signals: list[dict], 信号列表
        push:    是否推送卡片 (默认 False, 已关闭推送只写表格)
        sheet:   是否写入电子表格 (默认 True)
        bitable: 是否写入多维表格 (默认 True)

    Returns:
        dict: {'pushed': int, 'sheet_ok': bool, 'bitable_ok': bool}
    """
    if not signals:
        return {'pushed': 0, 'sheet_ok': True, 'bitable_ok': True}

    result = {'pushed': 0, 'sheet_ok': False, 'bitable_ok': False}

    # 1. 推送卡片 (逐条, 含频控; 自动识别信号/决策格式)
    if push:
        for s in signals:
            try:
                # decision 格式 (含 action) → push_decision; signal 格式 (含 signal_type) → push_signal
                if 'action' in s and 'signal_type' not in s:
                    if push_decision(s):
                        result['pushed'] += 1
                else:
                    if push_signal(s):
                        result['pushed'] += 1
            except Exception as e:
                _logger.warning('推送信号失败: %s', e)

    # 2. 写入电子表格 (批量; 未配 token 则自动创建)
    if sheet and _cfg.has_app_credentials():
        try:
            sid = auto_daily_sheet()
            if sid:
                st = _cfg.SHEET_TOKEN
                result['sheet_ok'] = write_signal_batch(st, sid, signals)
        except Exception as e:
            _logger.warning('写入 Sheet 失败: %s', e)

    # 3. 写入多维表格 (批量)
    if bitable and _cfg.BITABLE_TOKEN:
        try:
            tid = auto_daily_table(_cfg.BITABLE_TOKEN)
            if tid:
                result['bitable_ok'] = write_signal_batch_bitable(
                    _cfg.BITABLE_TOKEN, tid, signals)
        except Exception as e:
            _logger.warning('写入 Bitable 失败: %s', e)

    _logger.info('log_signals: %d 条, pushed=%d, sheet=%s, bitable=%s',
                 len(signals), result['pushed'],
                 result['sheet_ok'], result['bitable_ok'])
    return result


__all__ = [
    # push
    'push_text', 'push_signal', 'push_decision', 'send_to_chat',
    # doc
    'create_doc', 'append_to_doc', 'append_signal',
    'create_daily_report', 'get_doc_url',
    # sheet
    'append_rows', 'write_signal_batch', 'auto_daily_sheet',
    'ensure_headers', 'get_sheet_url', 'SIGNAL_HEADERS',
    # bitable
    'create_bitable', 'append_records', 'write_signal_batch_bitable',
    'auto_daily_table', 'get_bitable_url',
    # unified
    'log_signals',
    # auth
    'get_tenant_token', 'auth_headers',
    # config
    'has_app_credentials',
]
