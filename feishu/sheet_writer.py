"""飞书电子表格写入

功能:
  - append_rows:           追加行数据到指定子 sheet
  - write_signal_batch:    批量写入信号 (自动格式化字段)
  - auto_daily_sheet:      按日期自动创建/切换子 sheet
  - ensure_headers:        确保表头行存在

使用方式:
  1. 在飞书手动创建一个空电子表格, 将 token 填入 .env 的 LARK_SHEET_TOKEN
  2. 调用 auto_daily_sheet() 自动创建今日子 sheet
  3. 调用 write_signal_batch() 或 append_rows() 写入数据

攒批策略: 调用方负责攒批 (如每分钟 flush 一次), 本模块提供批量写入接口。
"""

import logging
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('feishu.config')
_auth = importlib.import_module('feishu.auth')

logger = logging.getLogger(__name__)

# 信号日志表头
SIGNAL_HEADERS = ['时间', '代码', '股票名称', '策略', '信号类型', '评分', '价格', '成交量', '原因']


def _api(method, path, body=None, params=None):
    """飞书 API 通用请求 (同 doc_writer, 为避免循环导入独立实现)"""
    headers = _auth.auth_headers()
    if not headers:
        logger.error('飞书 API 认证不可用, 跳过请求')
        return None
    url = f'{_cfg.BASE_URL}{path}'
    try:
        resp = requests.request(
            method, url, headers=headers,
            json=body, params=params, timeout=15,
        )
        data = resp.json()
        if data.get('code', -1) != 0:
            logger.error('飞书 API 错误 [%s %s]: %s', method, path, data)
            return None
        return data
    except Exception as e:
        logger.exception('飞书 API 异常 [%s %s]: %s', method, path, e)
        return None


# ══════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════

def append_rows(spreadsheet_token: str, sheet_id: str,
                rows: list, major_dimension: str = 'ROWS') -> bool:
    """追加行数据到指定子 sheet。

    Args:
        spreadsheet_token: 电子表格 token
        sheet_id: 子 sheet ID
        rows: 二维列表 [[col1, col2, ...], ...]
        major_dimension: 'ROWS' (按行追加) 或 'COLUMNS' (按列追加)

    Returns:
        bool: 是否成功
    """
    if not rows:
        return True
    path = f'/sheets/v2/spreadsheets/{spreadsheet_token}/values_append'
    body = {
        'valueRange': {
            'range': f'{sheet_id}!A:A',
            'values': rows,
        },
        'majorDimension': major_dimension,
    }
    data = _api('POST', path, body=body)
    if data:
        logger.info('追加 %d 行到表格 %s/%s', len(rows), spreadsheet_token, sheet_id)
        return True
    return False


def write_signal_batch(spreadsheet_token: str, sheet_id: str,
                       signals: list) -> bool:
    """批量写入信号到表格 (自动格式化字段)。

    Args:
        spreadsheet_token: 电子表格 token
        sheet_id: 子 sheet ID
        signals: list[dict], 同 push_signal 的 signal 字段列表

    Returns:
        bool: 是否成功
    """
    if not signals:
        return True
    rows = [_signal_to_row(s) for s in signals]
    return append_rows(spreadsheet_token, sheet_id, rows)


def auto_daily_sheet(spreadsheet_token: str = '') -> str:
    """按日期自动创建/切换子 sheet。

    查找名为今日日期 (如 "2026-07-05") 的子 sheet,
    不存在则创建。返回 sheet_id。

    如果 spreadsheet_token 未配置, 自动创建电子表格到指定文件夹。

    Args:
        spreadsheet_token: 电子表格 token (空则从 .env 读取, 仍无则自动创建)

    Returns:
        str: sheet_id; 失败返回空串
    """
    token = spreadsheet_token or _cfg.SHEET_TOKEN
    if not token:
        # 自动创建电子表格
        token = _ensure_spreadsheet()
        if not token:
            return ''

    today = datetime.now().strftime('%Y-%m-%d')

    # 1. 查询已有子 sheet 列表
    data = _api('GET', f'/sheets/v3/spreadsheets/{token}/sheets/query')
    if data:
        sheets = data.get('data', {}).get('sheets', [])
        for s in sheets:
            if s.get('title') == today:
                sheet_id = s.get('sheet_id', '')
                logger.info('找到已有子 sheet: %s (id=%s)', today, sheet_id)
                return sheet_id

    # 2. 不存在则创建
    body = {'title': today}
    data = _api('POST', f'/sheets/v3/spreadsheets/{token}/sheets', body=body)
    if not data:
        logger.error('创建子 sheet %s 失败', today)
        return ''

    sheet_id = data.get('data', {}).get('sheet', {}).get('sheet_id', '')
    if not sheet_id:
        logger.error('创建子 sheet 返回无 sheet_id: %s', data)
        return ''

    logger.info('已创建子 sheet: %s (id=%s)', today, sheet_id)

    # 3. 写入表头 (新创建的 sheet 直接强制写入)
    ensure_headers(token, sheet_id, SIGNAL_HEADERS, force=True)

    # 4. 应用格式 (冻结首行 + 列宽 + 表头样式 + 评分色阶)
    try:
        _apply_sheet_format(token, sheet_id, SIGNAL_HEADERS)
    except Exception as e:
        logger.warning('sheet 格式化失败 (不影响数据写入): %s', e)

    return sheet_id


def ensure_headers(spreadsheet_token: str, sheet_id: str,
                   headers: list, force: bool = False) -> bool:
    """确保表头行存在。

    Args:
        spreadsheet_token: 电子表格 token
        sheet_id: 子 sheet ID
        headers: 表头列表 ['时间', '代码', ...]
        force: 强制写入表头 (忽略已有内容检查)

    Returns:
        bool: 是否成功
    """
    if not force:
        # 读取 A1 看是否已有内容
        path = f'/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}!A1:A1'
        data = _api('GET', path)
        if data:
            value_range = data.get('data', {}).get('valueRange', {})
            values = value_range.get('values', [])
            if values and values[0]:
                # 已有内容, 不覆盖
                return True

    # 写入表头 (range 需指定结束列)
    end_col = chr(ord('A') + len(headers) - 1)  # 如 8 列 → H
    body = {
        'valueRange': {
            'range': f'{sheet_id}!A1:{end_col}1',
            'values': [headers],
        },
    }
    result = _api('PUT', f'/sheets/v2/spreadsheets/{spreadsheet_token}/values', body=body)
    return result is not None


def get_sheet_url(spreadsheet_token: str, sheet_id: str = '') -> str:
    """生成飞书表格可访问 URL"""
    base = f'https://bytedance.larkoffice.com/sheets/{spreadsheet_token}'
    if sheet_id:
        return f'{base}?sheet={sheet_id}'
    return base


# ══════════════════════════════════════════════════════════
# 内部实现
# ══════════════════════════════════════════════════════════

# 模块级缓存: 已自动创建的 spreadsheet token
_auto_sheet_token = ''


def _ensure_spreadsheet() -> str:
    """确保电子表格存在, 不存在则创建到指定文件夹。

    Returns:
        str: spreadsheet token
    """
    global _auto_sheet_token
    if _auto_sheet_token:
        return _auto_sheet_token
    # 检查 config 中是否已有 token (其他进程已创建)
    if _cfg.SHEET_TOKEN:
        _auto_sheet_token = _cfg.SHEET_TOKEN
        return _auto_sheet_token
    folder = _cfg.FOLDER_TOKEN
    body = {'title': '量化信号日志'}
    if folder:
        body['folder_token'] = folder
    data = _api('POST', '/sheets/v3/spreadsheets', body=body)
    if not data:
        logger.error('自动创建电子表格失败')
        return ''
    token = data.get('data', {}).get('spreadsheet', {}).get('spreadsheet_token', '')
    if not token:
        logger.error('创建电子表格返回无 token: %s', data)
        return ''
    _auto_sheet_token = token
    _cfg.SHEET_TOKEN = token  # 同步到 config, 后续复用
    # 设置权限: 组织内链接可编辑
    _set_public_permission(token)
    # 推送链接到飞书群
    _notify_link('电子表格', '量化信号日志', token)
    logger.info('已自动创建电子表格: token=%s', token)
    return token


def _signal_to_row(signal: dict) -> list:
    """将信号/决策 dict 转为一行数据 (与 SIGNAL_HEADERS 对齐)

    兼容两种格式:
      - signal: {signal_time, code, strategy_name, signal_type, signal_score, ...}
      - decision: {decision_time, code, strategy_name, action, position_size, ...}

    时间字段: 如果只有时分秒 (如 "09:35:00") 自动补上今日日期;
    如果已是完整日期时间则原样使用。
    """
    # 统一字段: decision 格式映射到 signal 格式
    raw_time = str(signal.get('decision_time', '') or signal.get('signal_time', ''))
    if raw_time and len(raw_time) <= 8 and ':' in raw_time:
        today = datetime.now().strftime('%Y-%m-%d')
        raw_time = f'{today} {raw_time}'
    signal_type = signal.get('action', '') or signal.get('signal_type', '')
    score = signal.get('signal_score', '') or signal.get('position_size', '')
    return [
        raw_time,
        str(signal.get('code', '')),
        str(signal.get('stock_name', '')),
        str(signal.get('strategy_name', '')),
        str(signal_type),
        str(score),
        str(signal.get('price', '') or ''),
        str(signal.get('volume', '') or ''),
        str(signal.get('reason', '')),
    ]


def _set_public_permission(spreadsheet_token: str):
    """设置电子表格为组织内链接可编辑"""
    body = {
        'external_access_entity': 'open',
        'security_entity': 'anyone_can_view',
        'comment_entity': 'anyone_can_view',
        'share_entity': 'anyone',
        'link_share_entity': 'tenant_editable',
        'invite_external': False,
    }
    _api('PATCH', f'/drive/v1/permissions/{spreadsheet_token}/public?type=sheet', body=body)


def _notify_link(res_type: str, name: str, spreadsheet_token: str):
    """推送新资源链接到飞书群"""
    try:
        _push = importlib.import_module('feishu.push')
        url = f'https://bytedance.larkoffice.com/sheets/{spreadsheet_token}'
        _push.push_text(f'📎 新{res_type}: {name}\n{url}')
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
# Sheet 格式化 (冻结/列宽/表头样式/条件格式)
# ══════════════════════════════════════════════════════════

# 列宽配置 (与 SIGNAL_HEADERS 对齐)
_COLUMN_WIDTHS = {
    'A': 140,   # 时间
    'B': 100,   # 代码
    'C': 100,   # 股票名称
    'D': 120,   # 策略
    'E': 80,    # 信号类型
    'F': 60,    # 评分
    'G': 80,    # 价格
    'H': 80,    # 成交量
    'I': 300,   # 原因 (最宽)
}


def _freeze_header_row(spreadsheet_token: str, sheet_id: str) -> bool:
    """冻结首行 (前 1 行)

    POST /sheets/v2/spreadsheets/{token}/dimension_range
    """
    body = {
        'dimension_range': {
            'sheet_id': sheet_id,
            'major_dimension': 'ROWS',
            'start_index': 0,
            'end_index': 1,
            'frozen': True,
        }
    }
    data = _api('POST', f'/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range', body=body)
    if data:
        logger.info('已冻结首行: %s', sheet_id)
        return True
    return False


def _set_column_widths(spreadsheet_token: str, sheet_id: str) -> bool:
    """设置列宽 (按 _COLUMN_WIDTHS 配置)"""
    all_ok = True
    for col, width in _COLUMN_WIDTHS.items():
        col_idx = ord(col) - ord('A')
        body = {
            'dimension_range': {
                'sheet_id': sheet_id,
                'major_dimension': 'COLUMNS',
                'start_index': col_idx,
                'end_index': col_idx + 1,
                'width': width,
            }
        }
        data = _api('POST', f'/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range', body=body)
        if not data:
            all_ok = False
            logger.warning('设置列宽失败: %s (%s)', col, sheet_id)
    if all_ok:
        logger.info('已设置列宽: %s', sheet_id)
    return all_ok


def _style_header(spreadsheet_token: str, sheet_id: str,
                  headers: list) -> bool:
    """表头加粗 + 蓝底白字

    通过 PUT /sheets/v2/spreadsheets/{token}/values_style 写入
    """
    end_col = chr(ord('A') + len(headers) - 1)
    body = {
        'valueRange': {
            'range': f'{sheet_id}!A1:{end_col}1',
            'values': [headers],
        },
        'style': {
            'bold': True,
            'background_color': '#1890FF',
            'font_color': '#FFFFFF',
            'horizontal_alignment': 'CENTER',
        },
    }
    data = _api('PUT',
                f'/sheets/v2/spreadsheets/{spreadsheet_token}/values_style',
                body=body)
    if data:
        logger.info('已设置表头样式: %s', sheet_id)
        return True
    return False


def _add_condition_format(spreadsheet_token: str, sheet_id: str) -> bool:
    """评分列(F)加色阶: 0=红, 60=黄, 100=绿

    POST /sheets/v2/spreadsheets/{token}/condition_formats
    """
    body = {
        'condition_format': {
            'range': f'{sheet_id}!F2:F1000',
            'rules': [
                {
                    'type': 'color_scale',
                    'min': {'type': 'num', 'value': 0,   'color': '#FF4D4F'},
                    'mid': {'type': 'num', 'value': 60,  'color': '#FAAD14'},
                    'max': {'type': 'num', 'value': 100, 'color': '#52C41A'},
                }
            ],
        }
    }
    data = _api('POST',
                f'/sheets/v2/spreadsheets/{spreadsheet_token}/condition_formats',
                body=body)
    if data:
        logger.info('已添加评分色阶: %s', sheet_id)
        return True
    return False


def _apply_sheet_format(spreadsheet_token: str, sheet_id: str,
                        headers: list = None):
    """一键应用全部格式 (冻结 + 列宽 + 表头 + 色阶)

    在 auto_daily_sheet 创建子 sheet 后调用一次。
    任何一步失败不影响其他 (容错)。
    """
    if not headers:
        headers = SIGNAL_HEADERS

    for fn_name, fn in [
        ('freeze',   lambda: _freeze_header_row(spreadsheet_token, sheet_id)),
        ('width',    lambda: _set_column_widths(spreadsheet_token, sheet_id)),
        ('header',   lambda: _style_header(spreadsheet_token, sheet_id, headers)),
        ('cond_fmt', lambda: _add_condition_format(spreadsheet_token, sheet_id)),
    ]:
        try:
            fn()
        except Exception as e:
            logger.warning('sheet 格式化 %s 失败: %s', fn_name, e)
