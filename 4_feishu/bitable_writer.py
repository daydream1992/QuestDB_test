"""飞书多维表格 (Bitable) 写入

功能:
  - create_bitable:         创建多维表格 + 信号表
  - append_records:         追加记录到数据表
  - write_signal_batch:     批量写入信号 (自动格式化字段)
  - auto_daily_table:       按日期自动创建/切换数据表

与 Sheet 的分工:
  - Sheet:  简单日志追加, 程序写入优先
  - Bitable: 结构化存储, 支持筛选/视图/仪表盘, 人工分析优先
"""

import logging
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('4_feishu.config')
_auth = importlib.import_module('4_feishu.auth')

logger = logging.getLogger(__name__)

# 信号表字段定义
SIGNAL_FIELDS = [
    {'field_name': '时间', 'type': 1},       # 1=文本
    {'field_name': '代码', 'type': 1},
    {'field_name': '股票名称', 'type': 1},
    {'field_name': '策略', 'type': 1},
    {'field_name': '信号类型', 'type': 3},    # 3=单选
    {'field_name': '评分', 'type': 2},        # 2=数字
    {'field_name': '价格', 'type': 2},
    {'field_name': '成交量', 'type': 2},
    {'field_name': '原因', 'type': 1},
]

# 信号类型选项 (单选字段的可选值)
SIGNAL_TYPE_OPTIONS = [
    'buy', 'sell', 'warn', 'observe', 'hold', 'stop_loss', 'stop_profit',
    'surge_up', 'surge_down', 'limit_seal', 'limit_break',
    'capital_in', 'capital_out',
]


def _api(method, path, body=None, params=None):
    """飞书 API 通用请求"""
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

def create_bitable(name: str = '量化信号', folder_token: str = '') -> dict:
    """创建多维表格 + 默认信号表。

    Args:
        name: 多维表格名称
        folder_token: 目标文件夹 token (空则从 config 读取)

    Returns:
        dict: {'app_token': '...', 'table_id': '...'} ; 失败返回空 dict
    """
    folder = folder_token or _cfg.FOLDER_TOKEN
    # 1. 创建多维表格
    body = {'name': name}
    if folder:
        body['folder_token'] = folder
    data = _api('POST', '/bitable/v1/apps', body=body)
    if not data:
        return {}
    app = data.get('data', {}).get('app', {})
    app_token = app.get('app_token', '')
    if not app_token:
        logger.error('创建多维表格返回无 app_token: %s', data)
        return {}
    logger.info('已创建多维表格: %s (token=%s)', name, app_token)
    # 设置权限 + 推送链接
    _set_public_permission(app_token)
    _notify_link('多维表格', name, app_token)

    # 2. 在默认数据表中添加字段 (默认表已有一个空表, 先获取 table_id)
    tables_data = _api('GET', f'/bitable/v1/apps/{app_token}/tables')
    if not tables_data:
        return {'app_token': app_token, 'table_id': ''}

    tables = tables_data.get('data', {}).get('items', [])
    if tables:
        # 用默认的第一张表
        table_id = tables[0].get('table_id', '')
    else:
        # 没有默认表, 创建一个
        table_id = _create_table(app_token, '信号记录')
        if not table_id:
            return {'app_token': app_token, 'table_id': ''}

    # 3. 添加字段 (跳过默认自带的「标题」等字段)
    _add_signal_fields(app_token, table_id)

    # 4. 设置权限为组织内可编辑
    _set_public_permission(app_token)

    return {'app_token': app_token, 'table_id': table_id}


def append_records(app_token: str, table_id: str, records: list) -> bool:
    """追加记录到数据表。

    Args:
        app_token: 多维表格 token
        table_id: 数据表 ID
        records: list[dict], 每条记录为 {字段名: 值}

    Returns:
        bool: 是否成功
    """
    if not records:
        return True
    body = {'records': [{'fields': r} for r in records]}
    data = _api('POST', f'/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create', body=body)
    if data:
        created = data.get('data', {}).get('records', [])
        logger.info('追加 %d 条记录到多维表格 %s/%s', len(created), app_token, table_id)
        return True
    return False


def write_signal_batch(app_token: str, table_id: str, signals: list) -> bool:
    """批量写入信号到多维表格 (自动格式化字段)。

    Args:
        app_token: 多维表格 token
        table_id: 数据表 ID
        signals: list[dict], 信号列表

    Returns:
        bool: 是否成功
    """
    if not signals:
        return True
    records = [_signal_to_record(s) for s in signals]
    return append_records(app_token, table_id, records)


def auto_daily_table(app_token: str) -> str:
    """按日期自动创建/切换数据表。

    查找名为今日日期 (如 "2026-07-05") 的数据表,
    不存在则创建并添加字段。返回 table_id。

    Args:
        app_token: 多维表格 token

    Returns:
        str: table_id; 失败返回空串
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # 1. 查询已有数据表
    data = _api('GET', f'/bitable/v1/apps/{app_token}/tables')
    if data:
        tables = data.get('data', {}).get('items', [])
        for t in tables:
            if t.get('name') == today:
                table_id = t.get('table_id', '')
                logger.info('找到已有数据表: %s (id=%s)', today, table_id)
                return table_id

    # 2. 不存在则创建
    table_id = _create_table(app_token, today)
    if not table_id:
        return ''

    # 3. 添加字段
    _add_signal_fields(app_token, table_id)
    return table_id


def get_bitable_url(app_token: str, table_id: str = '') -> str:
    """生成多维表格可访问 URL"""
    base = f'https://bytedance.larkoffice.com/base/{app_token}'
    if table_id:
        return f'{base}?table={table_id}'
    return base


# ══════════════════════════════════════════════════════════
# 内部实现
# ══════════════════════════════════════════════════════════

def _create_table(app_token: str, name: str) -> str:
    """在多维表格中创建数据表。返回 table_id。"""
    body = {'table': {'name': name}}
    data = _api('POST', f'/bitable/v1/apps/{app_token}/tables', body=body)
    if not data:
        return ''
    return data.get('data', {}).get('table_id', '')


def _add_signal_fields(app_token: str, table_id: str):
    """添加信号字段到数据表。

    处理逻辑:
      1. 获取已有字段, 主键字段 (Primary Field) 不可删除, 改名为第一个信号字段
      2. 删除其余默认字段
      3. 添加剩余自定义字段
    """
    existing_fields = []
    fields_data = _api('GET', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields')
    if fields_data:
        existing_fields = fields_data.get('data', {}).get('items', [])

    if not existing_fields:
        # 无已有字段, 直接添加
        for field_def in SIGNAL_FIELDS:
            _create_field(app_token, table_id, field_def)
        return

    # 找到主键字段 (第一个, 不可删除)
    primary = existing_fields[0]
    primary_id = primary.get('field_id', '')

    # 将主键字段改名为「时间」并改为文本类型
    _api('PUT', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary_id}',
         body={'field_name': '时间', 'type': 1})

    # 删除其余默认字段
    for f in existing_fields[1:]:
        fid = f.get('field_id', '')
        if fid:
            _api('DELETE', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{fid}')

    # 添加剩余自定义字段 (跳过第一个「时间」, 已通过主键改名处理)
    for field_def in SIGNAL_FIELDS[1:]:
        _create_field(app_token, table_id, field_def)


def _create_field(app_token: str, table_id: str, field_def: dict):
    """创建单个字段"""
    fname = field_def['field_name']
    body = {
        'field_name': fname,
        'type': field_def['type'],
    }
    if field_def['type'] == 3 and fname == '信号类型':
        body['property'] = {
            'options': [{'name': opt} for opt in SIGNAL_TYPE_OPTIONS],
        }
    _api('POST', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields', body=body)


def _signal_to_record(signal: dict) -> dict:
    """将信号/决策 dict 转为多维表格记录。

    兼容两种格式:
      - signal: {signal_time, code, strategy_name, signal_type, signal_score, ...}
      - decision: {decision_time, code, strategy_name, action, position_size, ...}

    时间字段: 只有时分秒时自动补上今日日期。
    """
    raw_time = str(signal.get('decision_time', '') or signal.get('signal_time', ''))
    if raw_time and len(raw_time) <= 8 and ':' in raw_time:
        today = datetime.now().strftime('%Y-%m-%d')
        raw_time = f'{today} {raw_time}'
    signal_type = signal.get('action', '') or signal.get('signal_type', '')
    score = signal.get('signal_score', None) or signal.get('position_size', None)

    return {
        '时间': raw_time,
        '代码': str(signal.get('code', '')),
        '股票名称': str(signal.get('stock_name', '')),
        '策略': str(signal.get('strategy_name', '')),
        '信号类型': str(signal_type),
        '评分': _safe_num(score),
        '价格': _safe_num(signal.get('price')),
        '成交量': _safe_num(signal.get('volume')),
        '原因': str(signal.get('reason', '')),
    }


def _safe_num(v) -> float:
    """安全转数字, 失败返回 0"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def _set_public_permission(app_token: str):
    """设置多维表格为组织内可编辑"""
    body = {
        'external_access_entity': 'open',
        'security_entity': 'anyone_can_view',
        'comment_entity': 'anyone_can_view',
        'share_entity': 'anyone',
        'link_share_entity': 'tenant_editable',
        'invite_external': False,
    }
    requests.patch(
        f'{_cfg.BASE_URL}/drive/v1/permissions/{app_token}/public',
        headers=_auth.auth_headers(),
        params={'type': 'bitable'},
        json=body,
        timeout=10,
    )


def _notify_link(res_type: str, name: str, app_token: str):
    """推送新资源链接到飞书群"""
    try:
        _push = importlib.import_module('4_feishu.push')
        url = f'https://bytedance.larkoffice.com/base/{app_token}'
        _push.push_text(f'📎 新{res_type}: {name}\n{url}')
    except Exception:
        pass
