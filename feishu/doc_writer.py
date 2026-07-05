"""飞书文档写入

功能:
  - create_doc:           创建飞书文档, 写入 Markdown 内容
  - append_to_doc:        追加 Markdown 内容到已有文档
  - append_signal:        追加单条信号到文档 (盘中实时)
  - create_daily_report:  创建日终策略报告文档 (按日期命名)

飞书文档 API 基于块 (block) 模型。本模块简化为 Markdown 写入,
通过飞书的文档创建接口直接导入 Markdown 内容。
"""

import json
import logging
from datetime import datetime

import requests

import importlib
_cfg = importlib.import_module('feishu.config')
_auth = importlib.import_module('feishu.auth')

logger = logging.getLogger(__name__)


def _api(method, path, body=None, params=None):
    """飞书 API 通用请求。

    Args:
        method: 'GET' / 'POST' / 'PUT' / 'PATCH'
        path: API 路径 (不含 BASE_URL)
        body: 请求体 dict
        params: 查询参数 dict

    Returns:
        dict: 响应 JSON; 失败返回 None
    """
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

def create_doc(title: str, content_md: str, folder_token: str = '') -> str:
    """创建飞书文档并写入内容。

    Args:
        title: 文档标题
        content_md: Markdown 格式内容
        folder_token: 目标文件夹 token (空则从 config 读取)

    Returns:
        str: 文档 ID; 失败返回空串
    """
    folder = folder_token or _cfg.FOLDER_TOKEN
    # 1. 创建空文档
    body = {'title': title}
    if folder:
        body['folder_token'] = folder
    data = _api('POST', '/docx/v1/documents', body=body)
    if not data:
        return ''
    doc = data.get('data', {}).get('document', {})
    doc_id = doc.get('document_id', '')
    if not doc_id:
        logger.error('创建文档返回无 document_id: %s', data)
        return ''
    logger.info('已创建飞书文档: %s (id=%s)', title, doc_id)
    # 设置权限: 组织内链接可编辑
    _set_public_permission(doc_id)
    # 推送链接到飞书群
    _notify_link('文档', title, doc_id)

    # 2. 写入内容 (通过 block children 接口追加)
    if content_md:
        ok = _write_blocks(doc_id, content_md)
        if not ok:
            logger.warning('文档已创建但内容写入失败: %s', doc_id)

    return doc_id


def append_to_doc(doc_id: str, content_md: str) -> bool:
    """追加 Markdown 内容到已有文档末尾。

    Args:
        doc_id: 飞书文档 ID
        content_md: Markdown 内容

    Returns:
        bool: 是否成功
    """
    if not doc_id:
        logger.error('doc_id 为空, 无法追加内容')
        return False
    return _write_blocks(doc_id, content_md)


def append_signal(doc_id: str, signal: dict) -> bool:
    """追加单条信号到文档 (盘中实时场景)。

    将信号格式化为简短 Markdown 行并追加。

    Args:
        doc_id: 飞书文档 ID
        signal: dict, 同 push_signal 的 signal 字段

    Returns:
        bool: 是否成功
    """
    ts = signal.get('signal_time', datetime.now().strftime('%H:%M:%S'))
    code = signal.get('code', '')
    stype = signal.get('signal_type', '')
    score = signal.get('signal_score', '')
    price = signal.get('price', '')
    reason = signal.get('reason', '')
    line = f'- **{ts}** {code} {stype} 评分:{score} 价:{price} {reason}'
    return append_to_doc(doc_id, line)


def create_daily_report(title_prefix: str = '策略日报',
                        content_md: str = '',
                        folder_token: str = '') -> str:
    """创建日终策略报告文档 (按日期命名)。

    Args:
        title_prefix: 标题前缀 (如 "策略日报")
        content_md: Markdown 报告内容
        folder_token: 目标文件夹 token

    Returns:
        str: 文档 ID; 失败返回空串
    """
    today = datetime.now().strftime('%Y-%m-%d')
    title = f'{title_prefix}_{today}'
    return create_doc(title, content_md, folder_token=folder_token)


def get_doc_url(doc_id: str) -> str:
    """生成飞书文档可访问 URL"""
    return f'https://bytedance.larkoffice.com/docx/{doc_id}'


# ══════════════════════════════════════════════════════════
# 内部实现
# ══════════════════════════════════════════════════════════

def _write_blocks(doc_id: str, content_md: str) -> bool:
    """将 Markdown 内容写入文档 (追加到 document_id 对应根 block 下)。

    飞书文档 block API: POST /docx/v1/documents/{id}/blocks/{block_id}/children
    将 Markdown 按 \n 分段, 每段一个 Text block。

    Returns:
        bool: 是否全部写入成功
    """
    if not content_md:
        return True

    # 飞书文档的 document_id 即为根 block_id
    path = f'/docx/v1/documents/{doc_id}/blocks/{doc_id}/children'

    # 按段落拆分, 过滤空行
    paragraphs = [p for p in content_md.split('\n') if p.strip()]
    if not paragraphs:
        return True

    # 构造 block children
    children = []
    for para in paragraphs:
        block = _md_para_to_block(para)
        if block:
            children.append(block)

    if not children:
        return True

    # 飞书 API 每次最多添加的 block 数量有限, 分批写入 (每批 50)
    batch_size = 50
    all_ok = True
    for i in range(0, len(children), batch_size):
        batch = children[i:i + batch_size]
        data = _api('POST', path, body={'children': batch})
        if not data:
            all_ok = False
            logger.warning('写入 block 批次 %d-%d 失败', i, i + len(batch))

    return all_ok


def _md_para_to_block(para: str) -> dict:
    """将单段 Markdown 文本转为飞书文档 block。

    支持检测标题 (# ~ ###) 和普通段落。
    """
    text = para.strip()

    # 标题检测
    if text.startswith('### '):
        return {
            'block_type': 5,  # heading3
            'heading3': {
                'elements': [{'text_run': {'content': text[4:]}}],
            },
        }
    if text.startswith('## '):
        return {
            'block_type': 4,  # heading2
            'heading2': {
                'elements': [{'text_run': {'content': text[3:]}}],
            },
        }
    if text.startswith('# '):
        return {
            'block_type': 3,  # heading1
            'heading1': {
                'elements': [{'text_run': {'content': text[2:]}}],
            },
        }

    # 普通段落 (含 **bold** 等 Markdown 语法, 飞书会渲染)
    return {
        'block_type': 2,  # text
        'text': {
            'elements': [{'text_run': {'content': text}}],
            'style': {},
        },
    }


def _set_public_permission(doc_id: str):
    """设置文档为组织内链接可编辑"""
    body = {
        'external_access_entity': 'open',
        'security_entity': 'anyone_can_view',
        'comment_entity': 'anyone_can_view',
        'share_entity': 'anyone',
        'link_share_entity': 'tenant_editable',
        'invite_external': False,
    }
    _api('PATCH', f'/drive/v1/permissions/{doc_id}/public?type=docx', body=body)


def _notify_link(res_type: str, title: str, doc_id: str):
    """推送新资源链接到飞书群"""
    try:
        _push = importlib.import_module('feishu.push')
        url = f'https://bytedance.larkoffice.com/docx/{doc_id}'
        _push.push_text(f'📎 新{res_type}: {title}\n{url}')
    except Exception:
        pass
