"""飞书多维表格 (Bitable) 写入

功能:
  - create_bitable:         创建多维表格 + 信号表
  - append_records:         追加记录到数据表
  - write_signal_batch:     批量写入信号 (自动格式化字段)
  - auto_daily_table:       按日期自动创建/切换数据表
  - auto_panorama_table:    按类型创建/切换全景数据表 (情绪/板块/打板/联动)
  - write_panorama_row:     写入全景情绪一行
  - write_heatmap_row:      写入板块热力图一行
  - write_ladder_row:       写入打板梯队一行
  - write_linkage_row:      写入板块联动一行

与 Sheet 的分工:
  - Sheet:  简单日志追加, 程序写入优先
  - Bitable: 结构化存储, 支持筛选/视图/仪表盘, 人工分析优先
"""

import logging
import time
import threading
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('feishu.config')
_auth = importlib.import_module('feishu.auth')

logger = logging.getLogger(__name__)

# ── auto_panorama_table 表名查询缓存 ──────────────────────────
# 模块级缓存: 避免每次写入都调 GET /bitable/v1/apps/{token}/tables
# 正结果缓存: (app_token, table_name) → table_id, 表名含日期跨日自然失效
# 负结果缓存: (app_token, table_name) → 失败时间戳, 5min TTL 避免短时重试
_TABLE_CACHE: dict[tuple, str] = {}
_TABLE_CACHE_NEG: dict[tuple, float] = {}
_TABLE_CACHE_TTL = 300  # 负结果 5min 失效
_TABLE_CACHE_LOCK = threading.Lock()  # 保护 check-then-act 原子性, 避免并发创建重复表

# 信号表字段定义 (升级版: 日期时间/单选/多选/复选/公式)
SIGNAL_FIELDS = [
    {'field_name': '时间', 'type': 5},            # 5=日期时间 (升级, 原为 1 文本)
    {'field_name': '代码', 'type': 1},            # 1=文本
    {'field_name': '股票名称', 'type': 1},
    {'field_name': '策略', 'type': 3},            # 3=单选 (升级, 原为 1 文本)
    {'field_name': '信号类型', 'type': 3},        # 3=单选 (保留)
    {'field_name': '评分', 'type': 2},            # 2=数字 (保留)
    {'field_name': '价格', 'type': 2},
    {'field_name': '成交量', 'type': 2},
    {'field_name': '原因', 'type': 1},
    # P0-5 修复: 以下为 Bitable 增强字段 (Sheet 没有)
    {'field_name': '涨跌幅%', 'type': 2},
    {'field_name': '板块', 'type': 4},            # 4=多选
    {'field_name': '是否涨停', 'type': 7},        # 7=复选框
    {'field_name': '决策桶时间', 'type': 3},      # 3=单选 (09:30/09:35...)
    {'field_name': '评分档位', 'type': 20},       # 20=公式 (优/良/中)
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


def _api(method, path, body=None, params=None, _retry=True):
    """飞书 API 通用请求 (含 token 失效重试)"""
    headers = _auth.auth_headers()
    if not headers:
        logger.error('飞书 API 认证不可用, 跳过请求')
        return None
    url = f'{_cfg.BASE_URL}{path}'
    try:
        resp = requests.request(
            method, url, headers=headers,
            json=body, params=params, timeout=5,  # 降低超时时间，防止卡死
        )
        data = resp.json()
        if _auth.is_token_invalid(data) and _retry:
            logger.warning('token 失效 (code=%s), 刷新后重试', data.get('code'))
            _auth.invalidate_token()
            return _api(method, path, body=body, params=params, _retry=False)
        if data.get('code', -1) != 0:
            logger.error('飞书 API 错误 [%s %s]: %s', method, path, data)
            return None
        return data
    except requests.exceptions.Timeout as e:
        logger.warning('飞书 API 超时 [%s %s]: %s', method, path, e)
        return None
    except requests.exceptions.RequestException as e:
        logger.warning('飞书 API 网络异常 [%s %s]: %s', method, path, e)
        return None
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

    # 写入前校验：只保留已存在的字段
    existing_fields = _list_fields(app_token, table_id)
    if not existing_fields:
        logger.warning('无法获取多维表格字段列表, 跳过写入 %d 条', len(records))
        return False

    known = {f['field_name'] for f in existing_fields}
    field_map = {f['field_name']: f for f in existing_fields}
    validated = []
    for r in records:
        cleaned = {k: v for k, v in r.items() if k in known}
        # 单字段类型不匹配: 剔除该字段而非整条记录 (避免一个无关字段丢失核心数据)
        for k in list(cleaned.keys()):
            v = cleaned[k]
            if not _validate_record_field({k: v}, field_map[k]):
                logger.warning('字段类型不匹配, 剔除: %s=%r (期望 type=%s)',
                               k, v, field_map[k].get('type'))
                del cleaned[k]
        if cleaned:
            validated.append(cleaned)

    if not validated:
        logger.warning('所有记录字段都不匹配, 跳过写入')
        return False

    body = {'records': [{'fields': r} for r in validated]}
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

    # 公式: 公式表达式 (飞书 API 用 formula_expression, 非 formula)
    elif ftype == 20:
        if fname == '评分档位':
            body['property'] = {'formula_expression': 'IF([评分]>=80,"优",IF([评分]>=60,"良","中"))'}
        elif 'formula_expression' in field_def:
            body['property'] = {'formula_expression': field_def['formula_expression']}
        elif 'formula' in field_def:
            body['property'] = {'formula_expression': field_def['formula']}

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
    # P0-1 修复: 板块字段降级提取 (metadata → signal.sectors → signal.sector_name → reason)
    sectors = _extract_sectors(signal, metadata)
    is_zt = metadata.get('is_zt', False) or signal_type in ('limit_seal', 'surge_up') or _infer_is_zt(signal)

    # 4. 决策桶时间 (5 分钟一档)
    bucket_time = _get_bucket_time(raw_time)

    return {
        '时间': ts_ms,
        '代码': str(signal.get('code', '')),
        '股票名称': str(signal.get('stock_name', '')),
        '策略': str(signal.get('strategy_name', '')),
        '信号类型': str(signal_type),
        '评分': _safe_num(score),
        '价格': _safe_num(signal.get('price')),
        '成交量': _safe_num(signal.get('volume')),
        '原因': str(signal.get('reason', '')),
        # P0-5 修复: 以下字段 Sheet 没有, Bitable 增强字段
        '涨跌幅%': _safe_num(change_pct),
        '板块': sectors if sectors else None,
        '是否涨停': bool(is_zt),
        '决策桶时间': bucket_time,
        # 评分档位是公式字段, 不写入值, 飞书自动算
    }


def _parse_time_to_ms(raw_time: str) -> int:
    """把时间字符串转成毫秒时间戳 (飞书日期时间字段要求)

    支持格式:
      - "09:35:12" → 补今日日期
      - "2026-07-06 09:35:12"
      - "2026-07-06T09:35:12"
      - 已是时间戳数字 → 直接用

    P0-6 修复: 解析失败时记录警告日志 (不再静默回退到当前时间)
    """
    if not raw_time:
        logger.warning('时间字段为空, 使用当前时间: raw_time=%r', raw_time)
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
    # 解析失败, 用当前时间 + 警告日志
    logger.warning('时间解析失败, 使用当前时间: raw_time=%r', raw_time)
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


def _extract_sectors(signal: dict, metadata: dict) -> list:
    """降级提取板块字段 (P0-1 修复)

    优先级:
      1. metadata.sectors (调用方传入)
      2. signal.sectors (直接传入)
      3. signal.sector_name (单个板块名)
      4. signal.board / signal.industry (其他可能的字段名)

    Returns:
        list[str]: 板块列表, 无可用值返回 []
    """
    # 1. metadata.sectors
    if metadata.get('sectors') and isinstance(metadata['sectors'], list):
        return metadata['sectors']
    # 2. signal.sectors
    if signal.get('sectors') and isinstance(signal['sectors'], list):
        return signal['sectors']
    # 3. signal.sector_name (单个)
    if signal.get('sector_name'):
        return [str(signal['sector_name'])]
    # 4. 其他字段名
    for key in ('board', 'industry', 'industry_name', 'concept'):
        if signal.get(key):
            return [str(signal[key])]
    return []


def _infer_is_zt(signal: dict) -> bool:
    """降级推断是否涨停 (P0-1 修复辅助)

    优先级:
      1. signal_type / action in (limit_seal, surge_up)
      2. reason 文本包含 '涨停'/'封板'/'+10%'

    Returns:
        bool
    """
    stype = signal.get('action', '') or signal.get('signal_type', '')
    if stype in ('limit_seal', 'surge_up'):
        return True
    reason = signal.get('reason', '') or ''
    return ('涨停' in reason) or ('封板' in reason) or ('+10%' in reason)


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


# 飞书字段类型常量
_FEISHU_TYPE_TEXT = 1       # 文本
_FEISHU_TYPE_NUMBER = 2     # 数字
_FEISHU_TYPE_SINGLE = 3     # 单选
_FEISHU_TYPE_MULTI = 4      # 多选
_FEISHU_TYPE_DATETIME = 5   # 日期时间
_FEISHU_TYPE_CHECKBOX = 7   # 复选框
_FEISHU_TYPE_FORMULA = 20   # 公式 (只读)

# 飞书类型 -> Python 校验器
_TYPE_VALIDATORS = {
    _FEISHU_TYPE_TEXT:     lambda v: isinstance(v, str),
    _FEISHU_TYPE_NUMBER:   lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    _FEISHU_TYPE_SINGLE:   lambda v: isinstance(v, str),
    _FEISHU_TYPE_MULTI:    lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    _FEISHU_TYPE_DATETIME: lambda v: isinstance(v, int),
    _FEISHU_TYPE_CHECKBOX: lambda v: isinstance(v, bool),
}


def _validate_record_field(record: dict, field_def: dict) -> bool:
    """校验记录字段类型是否匹配 (P0-3 修复)

    Args:
        record: {字段名: 值}
        field_def: {field_name, type, ...}

    Returns:
        bool: True=校验通过或字段值不存在
    """
    fname = field_def.get('field_name', '')
    ftype = field_def.get('type')
    value = record.get(fname)

    # 字段值为 None 视为有效 (写入空)
    if value is None:
        return True

    # 公式字段不应写入
    if ftype == _FEISHU_TYPE_FORMULA:
        logger.debug('跳过公式字段写入: %s', fname)
        return True

    validator = _TYPE_VALIDATORS.get(ftype)
    if validator is None:
        logger.warning('未知字段类型: %s type=%s (跳过校验)', fname, ftype)
        return True

    return validator(value)


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

# 板块联动字段
LINKAGE_FIELDS = [
    {'field_name': '时间', 'type': 5},
    {'field_name': '板块代码', 'type': 1},
    {'field_name': '板块名称', 'type': 1},
    {'field_name': '板块类型', 'type': 3},            # 3=单选
    {'field_name': '联动评分', 'type': 2},
    {'field_name': '净流入(亿)', 'type': 2},
    {'field_name': '涨停家数', 'type': 2},
    {'field_name': '龙头股', 'type': 1},
    {'field_name': '龙头涨幅%', 'type': 2},
    {'field_name': '共振板块', 'type': 1},
    {'field_name': '切换信号', 'type': 1},
]

_PANORAMA_TABLE_NAMES = {
    'sentiment': '情绪全景',
    'heatmap': '板块梯队',
    'ladder': '打板梯队',
    'linkage': '板块联动',
}


def auto_panorama_table(app_token: str, table_type: str) -> str:
    """按类型创建/切换全景数据表。

    table_type: 'sentiment' | 'heatmap' | 'ladder'
    每天一个表 (如 "情绪全景 2026-07-06"), 自动创建+加字段。
    返回 table_id; 失败返回 ''。

    优化: 模块级缓存 _TABLE_CACHE 命中后直接返回, 避免每次写入都调 GET /tables。
    表名含当天日期, 跨日时旧缓存 key 自然失效 (不会被新查询命中)。
    """
    today = datetime.now().strftime('%Y-%m-%d')
    base_name = _PANORAMA_TABLE_NAMES.get(table_type, table_type)
    table_name = f'{base_name} {today}'
    cache_key = (app_token, table_name)

    # 锁覆盖: 缓存 check → API 查表 → 创建表 → 缓存 write, 保证 check-then-act 原子
    # _create_field 在锁外执行 (避免多字段创建长时间持锁; 表已缓存, 其他线程拿到 id 后
    # 若字段未补齐, append_records 会因 _list_fields 校验跳过本轮写入, 下一轮再补)
    with _TABLE_CACHE_LOCK:
        # 1. 正结果缓存命中
        cached_id = _TABLE_CACHE.get(cache_key)
        if cached_id:
            return cached_id

        # 2. 负结果缓存命中 (5min 内创建失败过, 跳过避免短时重试)
        neg_ts = _TABLE_CACHE_NEG.get(cache_key)
        if neg_ts and (time.time() - neg_ts) < _TABLE_CACHE_TTL:
            return ''

        # 3. 查已有表
        data = _api('GET', f'/bitable/v1/apps/{app_token}/tables')
        if data:
            tables = data.get('data', {}).get('items', [])
            for t in tables:
                if t.get('name') == table_name:
                    table_id = t.get('table_id', '')
                    if table_id:
                        _TABLE_CACHE[cache_key] = table_id
                        logger.info('找到已有全景表: %s', table_name)
                        return table_id

        # 4. 创建
        table_id = _create_table(app_token, table_name)
        if not table_id:
            _TABLE_CACHE_NEG[cache_key] = time.time()
            return ''
        # 缓存先写入, 让其他线程能拿到 table_id (字段稍后补)
        _TABLE_CACHE[cache_key] = table_id

    # 加字段 (锁外, 避免长时间持锁阻塞其他线程)
    fields = {
        'sentiment': PANORAMA_FIELDS,
        'heatmap': HEATMAP_FIELDS,
        'ladder': LADDER_FIELDS,
        'linkage': LINKAGE_FIELDS,
    }.get(table_type, PANORAMA_FIELDS)
    for fd in fields:
        _create_field(app_token, table_id, fd)
    return table_id


def auto_named_table(app_token: str, table_name: str, fields: list) -> str:
    """按名查/建数据表 + 任意字段 (v10.2 通用版)。

    与 auto_panorama_table 的区别: 表名 + 字段由参数传入 (不依赖固定 PANORAMA_FIELDS),
    方便业务方自定义 schema。镜像其 check-then-act 原子模式 + 缓存。

    Args:
        fields: list[dict], 每项 {'field_name', 'type', ...可选 'options'/'property'}
                (首字段建议为 '时间' type=5, 会复用主键字段改名)
    Returns:
        table_id; 失败 ''。表名含日期则跨日缓存自然失效。
    """
    cache_key = (app_token, table_name)
    with _TABLE_CACHE_LOCK:
        cached_id = _TABLE_CACHE.get(cache_key)
        if cached_id:
            return cached_id
        neg_ts = _TABLE_CACHE_NEG.get(cache_key)
        if neg_ts and (time.time() - neg_ts) < _TABLE_CACHE_TTL:
            return ''
        # 查已有表
        data = _api('GET', f'/bitable/v1/apps/{app_token}/tables')
        if data:
            for t in data.get('data', {}).get('items', []):
                if t.get('name') == table_name:
                    table_id = t.get('table_id', '')
                    if table_id:
                        _TABLE_CACHE[cache_key] = table_id
                        return table_id
        # 创建
        table_id = _create_table(app_token, table_name)
        if not table_id:
            _TABLE_CACHE_NEG[cache_key] = time.time()
            return ''
        _TABLE_CACHE[cache_key] = table_id

    # 加字段 (锁外): 首字段复用主键改名, 其余新建 (避免主键空字段)
    _add_named_fields(app_token, table_id, fields)
    return table_id


def _add_named_fields(app_token: str, table_id: str, fields: list) -> None:
    """新表补字段: 主键(第一个自带字段)改名为 fields[0], 其余 _create_field。
    已存在的表假定字段齐 (append_records 会按 _list_fields 校验)。"""
    if not fields:
        return
    fd_data = _api('GET', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields')
    existing = fd_data.get('data', {}).get('items', []) if fd_data else []
    first = fields[0]
    if existing:
        primary_id = existing[0].get('field_id', '')
        body = {'field_name': first['field_name'], 'type': first.get('type', 1)}
        if first.get('type') == 5:
            body['property'] = {'date_formatter': 'yyyy/MM/dd HH:mm'}
        if primary_id:
            _api('PUT', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary_id}', body=body)
        rest = fields[1:]
    else:
        rest = fields
    for fd in rest:
        _create_field(app_token, table_id, fd)


def write_panorama_row(app_token: str, result: dict, ts: str | None = None) -> bool:
    """写入全景情绪一行 (每 5min)

    从 k4.run() 返回的 result 中提取字段写到飞书多维表格。

    Args:
        app_token: 多维表格 token
        result: k4.run() 返回的 dict
        ts: 可选时间字符串 (ISO 格式), 优先用 k4 计算时刻; None 则回退 datetime.now()

    Returns:
        bool: 是否成功
    """
    table_id = auto_panorama_table(app_token, 'sentiment')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(ts) if ts else _parse_time_to_ms(datetime.now().strftime('%H:%M'))
    pg = result.get('pg_index')
    sig = result.get('pg_signal', '')
    b = result.get('breadth', {})
    cf = result.get('capital_flow', {}) or {}
    mn_raw = cf.get('main_net')
    mn = (_safe_num(mn_raw) or 0) / 1e8
    turn = result.get('turning_point') or {}
    turn_str = f'{turn.get("type", "")}: {turn.get("action", "")}' if turn else ''

    # 四大指数 (避 000001.SH 与平安银行混淆, 用 999999.SH)
    idx = result.get('index_readings', {}) or {}
    index_map = {
        '999999.SH': '上证涨幅',
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


def write_heatmap_row(app_token: str, result: dict, ts: str | None = None) -> bool:
    """写入板块热力图一行 (每 5min)

    Args:
        ts: 可选时间字符串 (ISO 格式), 优先用 k4 计算时刻; None 则回退 datetime.now()
    """
    table_id = auto_panorama_table(app_token, 'heatmap')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(ts) if ts else _parse_time_to_ms(datetime.now().strftime('%H:%M'))

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


def write_ladder_row(app_token: str, result: dict, ts: str | None = None) -> bool:
    """写入打板梯队一行 (每 5min)

    Args:
        ts: 可选时间字符串 (ISO 格式), 优先用 k4 计算时刻; None 则回退 datetime.now()
    """
    table_id = auto_panorama_table(app_token, 'ladder')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(ts) if ts else _parse_time_to_ms(datetime.now().strftime('%H:%M'))
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


def write_linkage_row(app_token: str, result: dict) -> bool:
    """写入板块联动一行 (每 60s)

    从 k6.run() 返回的 result 中提取字段写到飞书多维表格。

    Args:
        app_token: 多维表格 token
        result: k6.run() 返回的 dict {
            linkage_scores: {block_code: score},
            stock_roles: {block_code: {龙头:[], 中军:[], ...}},
            alerts: {opportunity: [], risk: []}
        }

    Returns:
        bool: 是否成功
    """
    table_id = auto_panorama_table(app_token, 'linkage')
    if not table_id:
        return False
    ts_ms = _parse_time_to_ms(datetime.now().strftime('%H:%M'))

    linkage_scores = result.get('linkage_scores', {})
    stock_roles = result.get('stock_roles', {})
    block_metrics = result.get('block_metrics', {})

    # 按评分排序，取 Top 10 写入
    sorted_blocks = sorted(linkage_scores.items(), key=lambda x: -x[1])[:10]

    # 从 relation_graph 获取板块名称和类型
    try:
        from lib.relation_graph import _sector_meta
    except Exception:
        _sector_meta = {}

    records = []
    for block_code, score in sorted_blocks:
        roles = stock_roles.get(block_code, {})
        leaders = roles.get('龙头', [])
        metrics = block_metrics.get(block_code, {})

        # 提取龙头代码和涨幅（优先用 block_metrics）
        leader_code = metrics.get('leader_code', '')
        leader_change = metrics.get('leader_change', 0)
        if not leader_code and leaders:
            try:
                leader_str = leaders[0]  # "名称(code) +涨幅%"
                code_start = leader_str.find('(') + 1
                code_end = leader_str.find(')')
                if code_start > 0 and code_end > code_start:
                    leader_code = leader_str[code_start:code_end]
                    leader_change_str = leader_str.split('+')[-1].replace('%', '')
                    leader_change = _safe_num(leader_change_str)
            except Exception:
                pass

        # 从 _sector_meta 获取板块名称和类型
        meta = _sector_meta.get(block_code, {})
        block_name = meta.get('sector_name', block_code) if meta else block_code
        sector_type = meta.get('sector_type', '') if meta else ''

        record = {
            '时间': ts_ms,
            '板块代码': block_code,
            '板块名称': block_name,
            '板块类型': sector_type,
            '联动评分': _safe_num(score),
            '净流入(亿)': (_safe_num(metrics.get('net_inflow', 0)) or 0) / 1e4,  # 万元→亿
            '涨停家数': _safe_num(metrics.get('zt_count', 0)),
            '龙头股': leader_code,
            '龙头涨幅%': _safe_num(leader_change),
            '共振板块': '',
            '切换信号': '',
        }
        records.append(record)

    if records:
        return append_records(app_token, table_id, records)
    return False


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
