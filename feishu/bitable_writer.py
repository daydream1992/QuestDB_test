"""飞书多维表格 (Bitable) 写入

功能:
  - create_bitable:         创建多维表格 + 信号表
  - append_records:         追加记录到数据表
  - write_signal_batch:     批量写入信号 (自动格式化字段)
  - auto_daily_table:       按日期自动创建/切换数据表
  - auto_panorama_table:    按类型创建/切换全景数据表 (情绪/板块/打板)
  - write_panorama_row:     写入全景情绪一行
  - write_heatmap_row:      写入板块热力图一行
  - write_ladder_row:       写入打板梯队一行

与 Sheet 的分工:
  - Sheet:  简单日志追加, 程序写入优先
  - Bitable: 结构化存储, 支持筛选/视图/仪表盘, 人工分析优先
"""

import logging
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('feishu.config')
_auth = importlib.import_module('feishu.auth')

logger = logging.getLogger(__name__)

# 信号表字段定义 (升级版: 日期时间/单选/多选/复选/公式)
SIGNAL_FIELDS = [
    {'field_name': '时间', 'type': 5},            # 5=日期时间 (升级, 原为 1 文本)
    {'field_name': '代码', 'type': 1},            # 1=文本
    {'field_name': '股票名称', 'type': 1},
    {'field_name': '策略', 'type': 3},            # 3=单选 (升级, 原为 1 文本)
    {'field_name': '信号类型', 'type': 3},        # 3=单选 (保留)
    {'field_name': '评分', 'type': 2},            # 2=数字 (保留)
    {'field_name': '涨跌幅%', 'type': 2},         # 新增: 涨跌幅
    {'field_name': '价格', 'type': 2},
    {'field_name': '成交量', 'type': 2},
    {'field_name': '板块', 'type': 4},            # 新增: 4=多选
    {'field_name': '是否涨停', 'type': 7},        # 新增: 7=复选框
    {'field_name': '决策桶时间', 'type': 3},      # 新增: 3=单选 (09:30/09:35...)
    {'field_name': '评分档位', 'type': 20},       # 新增: 20=公式 (优/良/中)
    {'field_name': '原因', 'type': 1},
]

# 信号类型选项 (单选字段的可选值, 带颜色)
SIGNAL_TYPE_OPTIONS = [
    {'name': 'buy', 'color': 0},           # 0=绿色
    {'name': 'sell', 'color': 1},          # 1=红色
    {'name': 'warn', 'color': 2},          # 2=橙色
    {'name': 'observe', 'color': 3},       # 3=蓝色
    {'name': 'hold', 'color': 4},          # 4=灰色
    {'name': 'stop_loss', 'color': 1},
    {'name': 'stop_profit', 'color': 0},
    {'name': 'surge_up', 'color': 0},
    {'name': 'surge_down', 'color': 1},
    {'name': 'limit_seal', 'color': 0},
    {'name': 'limit_break', 'color': 1},
    {'name': 'capital_in', 'color': 0},
    {'name': 'capital_out', 'color': 1},
]

# 策略选项 (单选, 带颜色) — 从 strategy/plugins/ 自动收集
STRATEGY_OPTIONS = [
    {'name': 'zt_daban', 'color': 0},
    {'name': 'zha_fanbao', 'color': 1},
    {'name': 'break_pressure', 'color': 3},
    {'name': 'sector_rotation', 'color': 4},
    {'name': 'resonance', 'color': 2},
    {'name': 'divergence', 'color': 2},
    {'name': 'dark_money', 'color': 4},
    {'name': 'auction_rush', 'color': 0},
    {'name': 'auction_gap', 'color': 3},
    {'name': 'auction_close', 'color': 3},
    {'name': 'big_order', 'color': 0},
    {'name': 'lhb_inst', 'color': 4},
    {'name': 'lhb_hotmoney', 'color': 2},
    {'name': 'stop_loss', 'color': 1},
    {'name': 'stop_profit', 'color': 0},
    {'name': 'market_emotion', 'color': 3},
    {'name': 'turn_alert', 'color': 2},
    {'name': 'alpha_breakout', 'color': 0},
    {'name': 'leader_echelon', 'color': 0},
    {'name': 'sector_rotation_relay', 'color': 3},
    {'name': 'capital_flow_divergence', 'color': 2},
    {'name': 'lhb_broker_network', 'color': 4},
    {'name': 'sentiment_extreme_reversal', 'color': 2},
    {'name': 'late_session_raid', 'color': 0},
]

# 决策桶时间选项 (每 5 分钟一档, 09:30 ~ 15:00)
BUCKET_TIME_OPTIONS = [f'{h:02d}:{m:02d}' for h in range(9, 15) for m in range(0, 60, 5) if not (h == 9 and m < 30) and not (h == 15 and m > 0)]


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

    # failed — retry with unknown fields removed
    if data is None and records:
        try:
            existing = _list_fields(app_token, table_id)
            if existing:
                known = {f['field_name'] for f in existing}
                cleaned = [{k: v for k, v in r.items() if k in known} for r in records]
                if cleaned:
                    body2 = {'records': [{'fields': r} for r in cleaned]}
                    data2 = _api('POST', f'/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create', body=body2)
                    if data2:
                        logger.info('追加 %d 条(字段裁剪后)到多维表格 %s/%s', len(cleaned), app_token, table_id)
                        return True
        except Exception as e:
            logger.warning('字段裁剪重试失败: %s', e)
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

    # 4. 创建预设视图 (今日实时 / 按策略分组 / 优档信号)
    try:
        create_signal_views(app_token, table_id)
    except Exception as e:
        logger.warning('创建视图失败 (不影响数据写入): %s', e)

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

    # 将主键字段改名为「时间」并改为日期时间类型 (type=5)
    _api('PUT', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary_id}',
         body={
             'field_name': '时间',
             'type': 5,
             'property': {'date_formatter': 'yyyy/MM/dd HH:mm'},
         })

    # 删除其余默认字段
    for f in existing_fields[1:]:
        fid = f.get('field_id', '')
        if fid:
            _api('DELETE', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{fid}')

    # 添加剩余自定义字段 (跳过第一个「时间」, 已通过主键改名处理)
    for field_def in SIGNAL_FIELDS[1:]:
        _create_field(app_token, table_id, field_def)


def _create_field(app_token: str, table_id: str, field_def: dict):
    """创建单个字段 (支持单选/多选/复选/公式)

    Args:
        field_def: dict, 必须含 field_name + type, 可选 property
            - type=3 单选: property={'options': [{'name': 'x', 'color': 0}, ...]}
            - type=4 多选: property={'options': [{'name': 'x', 'color': 0}, ...]}
            - type=5 日期时间: property={'date_formatter': 'yyyy/MM/dd HH:mm'}
            - type=7 复选框: property={'symbol': '✅'}
            - type=20 公式: property={'formula': 'IF(...)'}

    飞书 bitable 字段类型:
        1=文本, 2=数字, 3=单选, 4=多选, 5=日期时间, 7=复选框,
        11=人员, 13=电话, 15=超链接, 18=单向关联, 19=查找引用,
        20=公式, 21=双向关联, 22=位置, 23=群组, 1001=创建时间,
        1002=最后更新时间, 1003=创建人, 1004=修改人
    """
    fname = field_def['field_name']
    ftype = field_def['type']
    body = {
        'field_name': fname,
        'type': ftype,
    }

    # 单选/多选: 带 options
    if ftype in (3, 4):
        if fname == '信号类型':
            body['property'] = {'options': SIGNAL_TYPE_OPTIONS}
        elif fname == '策略':
            body['property'] = {'options': STRATEGY_OPTIONS}
        elif fname == '决策桶时间':
            body['property'] = {'options': [{'name': t} for t in BUCKET_TIME_OPTIONS]}
        elif 'options' in field_def:
            body['property'] = {'options': field_def['options']}

    # 日期时间: 格式化
    elif ftype == 5:
        body['property'] = {'date_formatter': 'yyyy/MM/dd HH:mm'}

    # 复选框: 符号
    elif ftype == 7:
        body['property'] = {'symbol': '✅'}

    # 公式: 公式表达式
    elif ftype == 20:
        if fname == '评分档位':
            body['property'] = {'formula': 'IF([评分]>=80,"优",IF([评分]>=60,"良","中"))'}
        elif 'formula' in field_def:
            body['property'] = {'formula': field_def['formula']}

    _api('POST', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields', body=body)


def _signal_to_record(signal: dict) -> dict:
    """将信号/决策 dict 转为多维表格记录 (升级版)

    兼容两种格式:
      - signal: {signal_time, code, strategy_name, signal_type, signal_score, ...}
      - decision: {decision_time, code, strategy_name, action, position_size, ...}

    升级点:
      - 时间字段转成毫秒时间戳 (飞书日期时间字段要求)
      - 涨跌幅/板块/是否涨停/决策桶时间 从 metadata 或 reason 提取
    """
    # 1. 时间 → 毫秒时间戳 (飞书日期时间字段 type=5 要求)
    raw_time = str(signal.get('decision_time', '') or signal.get('signal_time', ''))
    ts_ms = _parse_time_to_ms(raw_time)

    # 2. 信号类型/评分/价格
    signal_type = signal.get('action', '') or signal.get('signal_type', '')
    score = signal.get('signal_score', None) or signal.get('position_size', None)

    # 3. 从 metadata 提取升级字段 (调用方需在 metadata 里带上)
    metadata = signal.get('metadata', {}) or {}
    change_pct = metadata.get('change_pct') or _extract_change_pct(signal.get('reason', ''))
    sectors = metadata.get('sectors', []) or []
    is_zt = metadata.get('is_zt', False) or signal_type in ('limit_seal', 'surge_up')

    # 4. 决策桶时间 (5 分钟一档)
    bucket_time = _get_bucket_time(raw_time)

    return {
        '时间': ts_ms,
        '代码': str(signal.get('code', '')),
        '股票名称': str(signal.get('stock_name', '')),
        '策略': str(signal.get('strategy_name', '')),
        '信号类型': str(signal_type),
        '评分': _safe_num(score),
        '涨跌幅%': _safe_num(change_pct),
        '价格': _safe_num(signal.get('price')),
        '成交量': _safe_num(signal.get('volume')),
        '板块': sectors if sectors else None,   # 多选: 传 list
        '是否涨停': bool(is_zt),                 # 复选框: 传 bool
        '决策桶时间': bucket_time,               # 单选: 传字符串
        # 评分档位是公式字段, 不写入值, 飞书自动算
        '原因': str(signal.get('reason', '')),
    }


def _parse_time_to_ms(raw_time: str) -> int:
    """把时间字符串转成毫秒时间戳 (飞书日期时间字段要求)

    支持格式:
      - "09:35:12" → 补今日日期
      - "2026-07-06 09:35:12"
      - "2026-07-06T09:35:12"
      - 已是时间戳数字 → 直接用
    """
    if not raw_time:
        return int(datetime.now().timestamp() * 1000)
    # 已是数字
    try:
        n = float(raw_time)
        if n > 1e12:   # 已是毫秒
            return int(n)
        if n > 1e9:    # 是秒
            return int(n * 1000)
    except (TypeError, ValueError):
        pass
    # 只有时分秒
    if len(raw_time) <= 8 and ':' in raw_time:
        today = datetime.now().strftime('%Y-%m-%d')
        raw_time = f'{today} {raw_time}'
    # 尝试解析
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt = datetime.strptime(raw_time, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    # 解析失败, 用当前时间
    return int(datetime.now().timestamp() * 1000)


def _extract_change_pct(reason: str):
    """从 reason 字段中提取涨跌幅 (如 '+9.98%' → 9.98)"""
    if not reason:
        return None
    import re
    m = re.search(r'([+-]?\d+\.?\d*)%', reason)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _get_bucket_time(raw_time: str) -> str:
    """从时间字符串提取 5 分钟桶时间 (如 '09:37:xx' → '09:35')"""
    if not raw_time:
        return ''
    # 提取 HH:MM
    import re
    m = re.search(r'(\d{2}):(\d{2})', raw_time)
    if not m:
        return ''
    h, mn = int(m.group(1)), int(m.group(2))
    # 5 分钟桶: 09:37 → 09:35, 09:34 → 09:30
    bucket_min = (mn // 5) * 5
    return f'{h:02d}:{bucket_min:02d}'


def _safe_num(v) -> float | None:
    """安全转数字, 失败返回 None (区别于 0, 避免写入脏数据)"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
        _push = importlib.import_module('feishu.push')
        url = f'https://bytedance.larkoffice.com/base/{app_token}'
        _push.push_text(f'📎 新{res_type}: {name}\n{url}')
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 全景数据表 (k4 情绪/板块/打板 每 5min 行)
# ══════════════════════════════════════════════════════════════

# 情绪全景表字段
PANORAMA_FIELDS = [
    {'field_name': '时间', 'type': 5},            # 5=日期时间 (升级)
    {'field_name': 'PG指数', 'type': 2},
    {'field_name': 'PG信号', 'type': 3, 'options': [   # 3=单选 (与 k4_sentiment._pg_label 一致)
        {'name': '恐慌', 'color': 1},
        {'name': '恐惧', 'color': 4},
        {'name': '中性', 'color': 3},
        {'name': '贪婪', 'color': 2},
        {'name': '狂热', 'color': 0},
    ]},
    {'field_name': '涨停数', 'type': 2},
    {'field_name': '跌停数', 'type': 2},
    {'field_name': '封板率', 'type': 2},
    {'field_name': '涨跌比', 'type': 2},
    {'field_name': '涨家数', 'type': 2},
    {'field_name': '跌家数', 'type': 2},
    {'field_name': '主力净流(亿)', 'type': 2},
    {'field_name': '背离数', 'type': 2},
    {'field_name': '拐点信号', 'type': 1},
    {'field_name': '上证涨幅', 'type': 2},
    {'field_name': '深证涨幅', 'type': 2},
    {'field_name': '创业板涨幅', 'type': 2},
    {'field_name': '科创50涨幅', 'type': 2},
]

# 板块梯队表字段
HEATMAP_FIELDS = [
    {'field_name': '时间', 'type': 5},            # 5=日期时间 (升级)
    {'field_name': '行业Top1', 'type': 1},
    {'field_name': '行业Top1涨幅', 'type': 2},
    {'field_name': '行业Top1涨停', 'type': 2},
    {'field_name': '行业二级Top1', 'type': 1},
    {'field_name': '行业二级Top1涨幅', 'type': 2},
    {'field_name': '行业二级Top1涨停', 'type': 2},
    {'field_name': '行业三级Top1', 'type': 1},
    {'field_name': '行业三级Top1涨幅', 'type': 2},
    {'field_name': '行业三级Top1涨停', 'type': 2},
    {'field_name': '概念Top1', 'type': 1},
    {'field_name': '概念Top1涨幅', 'type': 2},
    {'field_name': '最强个股', 'type': 1},
    {'field_name': '最强个股涨幅', 'type': 2},
    {'field_name': '最强个股涨停', 'type': 1},
]

# 打板梯队表字段
LADDER_FIELDS = [
    {'field_name': '时间', 'type': 5},            # 5=日期时间 (升级)
    {'field_name': '首板数', 'type': 2},
    {'field_name': '二板数', 'type': 2},
    {'field_name': '三板数', 'type': 2},
    {'field_name': '四板数', 'type': 2},
    {'field_name': '五板+数', 'type': 2},
    {'field_name': '候选2进3', 'type': 2},
    {'field_name': '最强标的', 'type': 1},
    {'field_name': '晋级评分', 'type': 2},
    {'field_name': '候选股票', 'type': 1},
    {'field_name': '封单额', 'type': 2},
    {'field_name': '封成比', 'type': 2},
    {'field_name': '连板率', 'type': 2},
    {'field_name': '板块强度', 'type': 2},
]

_PANORAMA_TABLE_NAMES = {
    'sentiment': '情绪全景',
    'heatmap': '板块梯队',
    'ladder': '打板梯队',
}


def auto_panorama_table(app_token: str, table_type: str) -> str:
    """按类型创建/切换全景数据表。

    table_type: 'sentiment' | 'heatmap' | 'ladder'
    每天一个表 (如 "情绪全景 2026-07-06"), 自动创建+加字段。
    返回 table_id; 失败返回 ''。
    """
    today = datetime.now().strftime('%Y-%m-%d')
    base_name = _PANORAMA_TABLE_NAMES.get(table_type, table_type)
    table_name = f'{base_name} {today}'

    # 查已有表
    data = _api('GET', f'/bitable/v1/apps/{app_token}/tables')
    if data:
        tables = data.get('data', {}).get('items', [])
        for t in tables:
            if t.get('name') == table_name:
                logger.info('找到已有全景表: %s', table_name)
                return t.get('table_id', '')

    # 创建
    table_id = _create_table(app_token, table_name)
    if not table_id:
        return ''

    # 加字段
    fields = {
        'sentiment': PANORAMA_FIELDS,
        'heatmap': HEATMAP_FIELDS,
        'ladder': LADDER_FIELDS,
    }.get(table_type, PANORAMA_FIELDS)
    for fd in fields:
        _create_field(app_token, table_id, fd)
    return table_id


def write_panorama_row(app_token: str, result: dict) -> bool:
    """写入全景情绪一行 (每 5min)

    从 k4.run() 返回的 result 中提取字段写到飞书多维表格。

    Args:
        app_token: 多维表格 token
        result: k4.run() 返回的 dict

    Returns:
        bool: 是否成功
    """
    table_id = auto_panorama_table(app_token, 'sentiment')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(datetime.now().strftime('%H:%M'))
    pg = result.get('pg_index')
    sig = result.get('pg_signal', '')
    b = result.get('breadth', {})
    cf = result.get('capital_flow', {}) or {}
    mn_raw = cf.get('main_net')
    mn = (_safe_num(mn_raw) or 0) / 1e8
    turn = result.get('turning_point') or {}
    turn_str = f'{turn.get("type", "")}: {turn.get("action", "")}' if turn else ''

    # 四大指数
    idx = result.get('index_readings', {}) or {}
    index_map = {
        '000001.SH': '上证涨幅',
        '399001.SZ': '深证涨幅',
        '399006.SZ': '创业板涨幅',
        '000688.SH': '科创50涨幅',
    }

    record = {
        '时间': ts_ms,
        'PG指数': _safe_num(pg),
        'PG信号': sig if sig else None,  # 空字符串不写入单选字段，避免 option 不匹配
        '涨停数': _safe_num(b.get('zt_cnt')),
        '跌停数': _safe_num(b.get('dt_cnt')),
        '封板率': _safe_num(b.get('fbl')),
        '涨跌比': _safe_num(b.get('udr')),
        '涨家数': _safe_num(b.get('up_cnt')),
        '跌家数': _safe_num(b.get('down_cnt')),
        '主力净流(亿)': round(mn, 2),
        '背离数': _safe_num(result.get('divergence_count')),
        '拐点信号': turn_str,
    }
    # 四大指数
    for code, field_name in index_map.items():
        v = idx.get(code, {}) or {}
        record[field_name] = _safe_num(v.get('zaf'))
    return append_records(app_token, table_id, [record])


def write_heatmap_row(app_token: str, result: dict) -> bool:
    """写入板块热力图一行 (每 5min)"""
    table_id = auto_panorama_table(app_token, 'heatmap')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(datetime.now().strftime('%H:%M'))

    def _top1(ranking):
        return (ranking or [{}])[0]

    l1 = _top1(result.get('industry_l1_ranking'))
    l2 = _top1(result.get('industry_l2_ranking'))
    l3 = _top1(result.get('industry_l3_ranking'))
    concept = _top1(result.get('concept_ranking'))
    # 最强个股: 行业一级最强板块的个股梯队 Top1
    l1_stocks = result.get('industry_l1_stocks') or []
    best_stock = l1_stocks[0] if l1_stocks else {}

    record = {
        '时间': ts_ms,
        '行业Top1': l1.get('name', ''),
        '行业Top1涨幅': _safe_num(l1.get('zaf')),
        '行业Top1涨停': _safe_num(l1.get('zt_count')),
        '行业二级Top1': l2.get('name', ''),
        '行业二级Top1涨幅': _safe_num(l2.get('zaf')),
        '行业二级Top1涨停': _safe_num(l2.get('zt_count')),
        '行业三级Top1': l3.get('name', ''),
        '行业三级Top1涨幅': _safe_num(l3.get('zaf')),
        '行业三级Top1涨停': _safe_num(l3.get('zt_count')),
        '概念Top1': concept.get('name', ''),
        '概念Top1涨幅': _safe_num(concept.get('zaf')),
        '最强个股': best_stock.get('name', ''),
        '最强个股涨幅': _safe_num(best_stock.get('zaf')),
        '最强个股涨停': '📈涨停' if best_stock.get('is_zt') else '',
    }
    return append_records(app_token, table_id, [record])


def write_ladder_row(app_token: str, result: dict) -> bool:
    """写入打板梯队一行 (每 5min)"""
    table_id = auto_panorama_table(app_token, 'ladder')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(datetime.now().strftime('%H:%M'))
    stats = result.get('stats', {})
    candidates = result.get('promotion_rankings', [])
    best = candidates[0] if candidates else {}
    best_detail = best.get('detail', {}) if best else {}

    record = {
        '时间': ts_ms,
        '首板数': _safe_num(stats.get('total_1b')),
        '二板数': _safe_num(stats.get('total_2b')),
        '三板数': _safe_num(stats.get('total_3b')),
        '四板数': _safe_num(stats.get('total_4b')),
        '五板+数': _safe_num(stats.get('total_5b_plus')),
        '候选2进3': _safe_num(stats.get('candidates_2to3')),
        '最强标的': best.get('name', ''),
        '晋级评分': _safe_num(best.get('total_score')),
        '候选股票': best.get('name', ''),
        '封单额': _safe_num(best_detail.get('fcamo_score')),
        '封成比': _safe_num(best_detail.get('fcb_score')),
        '连板率': _safe_num(best_detail.get('lb_rate_score')),
        '板块强度': _safe_num(best_detail.get('sector_score')),
    }
    return append_records(app_token, table_id, [record])


# ══════════════════════════════════════════════════════════
# 信号表视图配置 (3 视图: 实时/分组/优档)
# ══════════════════════════════════════════════════════════

# 视图定义 (3 个)
SIGNAL_VIEWS = [
    {
        'view_name': '今日实时',
        'view_type': 'grid',
        'description': '按时间倒序, 一眼看到最新信号',
        'config': {
            'sort': [{'field_name': '时间', 'desc': True}],
            'filter': {
                'conjunction': 'and',
                'conditions': [
                    {'field_name': '决策桶时间', 'operator': 'isNot', 'value': ['']},
                ],
            },
            'hidden_fields': ['成交量', '决策桶时间'],
        },
    },
    {
        'view_name': '按策略分组',
        'view_type': 'grid',
        'description': '按策略分组, 组内按评分降序',
        'config': {
            'group': [{'field_name': '策略', 'desc': False}],
            'sort': [{'field_name': '评分', 'desc': True}],
            'hidden_fields': ['策略'],
        },
    },
    {
        'view_name': '优档信号',
        'view_type': 'grid',
        'description': '只看评分档位=优 的信号',
        'config': {
            'filter': {
                'conjunction': 'and',
                'conditions': [
                    {'field_name': '评分档位', 'operator': 'is', 'value': ['优']},
                ],
            },
            'sort': [{'field_name': '时间', 'desc': True}],
        },
    },
]


def create_signal_views(app_token: str, table_id: str) -> bool:
    """为信号表创建 3 个预设视图

    在 auto_daily_table 建表 + 加字段后调用一次。
    已存在的视图会跳过 (按 view_name 去重)。
    """
    existing_views = _list_views(app_token, table_id)
    existing_names = {v.get('view_name') for v in existing_views}

    all_ok = True
    for view_def in SIGNAL_VIEWS:
        vname = view_def['view_name']
        if vname in existing_names:
            logger.info('视图已存在, 跳过: %s', vname)
            continue

        view_id = _create_view(app_token, table_id, vname, view_def['view_type'])
        if not view_id:
            all_ok = False
            continue

        try:
            _apply_view_config(app_token, table_id, view_id, view_def['config'])
            logger.info('已创建视图: %s (id=%s)', vname, view_id)
        except Exception as e:
            logger.warning('视图 %s 配置失败: %s', vname, e)
            all_ok = False

    return all_ok


def _list_fields(app_token: str, table_id: str) -> list:
    """查询数据表的所有字段"""
    data = _api('GET', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields')
    if not data:
        return []
    return data.get('data', {}).get('items', [])


def _list_views(app_token: str, table_id: str) -> list:
    """查询数据表的所有视图"""
    data = _api('GET', f'/bitable/v1/apps/{app_token}/tables/{table_id}/views')
    if not data:
        return []
    return data.get('data', {}).get('items', [])


def _create_view(app_token: str, table_id: str,
                 view_name: str, view_type: str = 'grid') -> str:
    """创建视图, 返回 view_id"""
    body = {'view_name': view_name, 'view_type': view_type}
    data = _api('POST',
                f'/bitable/v1/apps/{app_token}/tables/{table_id}/views',
                body=body)
    if not data:
        return ''
    return data.get('data', {}).get('view_id', '')


def _apply_view_config(app_token: str, table_id: str, view_id: str,
                       config: dict):
    """应用视图配置 (排序/筛选/分组/隐藏字段)

    PATCH /bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}
    """
    body = {}
    if 'sort' in config:
        body['sort'] = config['sort']
    if 'filter' in config:
        body['filter'] = config['filter']
    if 'group' in config:
        body['group'] = config['group']
    if 'hidden_fields' in config:
        body['hidden_fields'] = config['hidden_fields']

    if not body:
        return

    _api('PATCH',
         f'/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}',
         body=body)


def get_default_view_id(app_token: str, table_id: str) -> str:
    """获取数据表的默认视图 ID (第一个视图)"""
    views = _list_views(app_token, table_id)
    if views:
        return views[0].get('view_id', '')
    return ''
