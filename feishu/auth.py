"""feishu.auth: 飞书 tenant_access_token 管理

脚本路径: K:\\QuestDB_test\\feishu\\auth.py
用途: 自动获取/刷新 tenant_access_token (2h 有效, 提前 5min 刷新)
依赖: 标准库 time/threading, 第三方 requests
配置: LARK_APP_ID / LARK_APP_SECRET (从 config/.env 读)
入参: FeishuAuth(app_id, app_secret)
返回: get_token() -> str (线程安全, 缓存自动续期)
说明:
  - threading.Lock 保护并发
  - 提前 5 分钟续期, 避免边缘 case
"""

import time
import logging
import threading

import requests

import importlib
_cfg = importlib.import_module('feishu.config')

logger = logging.getLogger(__name__)

# 内存缓存
_cache = {'token': '', 'expires_at': 0.0}
_lock = threading.Lock()


def get_tenant_token() -> str:
    """获取有效的 tenant_access_token, 自动刷新。

    Returns:
        str: 有效 token; 凭据未配或获取失败返回空串。
    """
    with _lock:
        now = time.time()
        # 缓存未过期 (提前 300s 刷新)
        if _cache['token'] and now < _cache['expires_at'] - 300:
            return _cache['token']

        if not _cfg.has_app_credentials():
            logger.debug('飞书应用凭据未配置, 跳过 token 获取')
            return ''

        try:
            resp = requests.post(
                f'{_cfg.BASE_URL}/auth/v3/tenant_access_token/internal',
                json={'app_id': _cfg.APP_ID, 'app_secret': _cfg.APP_SECRET},
                timeout=10,
            )
            data = resp.json()
            if data.get('code', -1) != 0:
                logger.error('获取 tenant_access_token 失败: %s', data)
                return ''
            _cache['token'] = data['tenant_access_token']
            _cache['expires_at'] = now + data.get('expire', 7200)
            logger.info('飞书 tenant_access_token 已刷新, 有效期 %ds',
                        data.get('expire', 7200))
            return _cache['token']
        except Exception as e:
            logger.exception('获取 tenant_access_token 异常: %s', e)
            return ''


def auth_headers() -> dict:
    """返回带 Authorization 的请求头。

    Returns:
        dict: {'Authorization': 'Bearer xxx', 'Content-Type': 'application/json'}
              凭据不可用时返回空 dict。
    """
    token = get_tenant_token()
    if not token:
        return {}
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=utf-8',
    }


def invalidate_token():
    """强制清除缓存, 下次请求重新获取 (用于 token 失效时)"""
    with _lock:
        _cache['token'] = ''
        _cache['expires_at'] = 0.0
