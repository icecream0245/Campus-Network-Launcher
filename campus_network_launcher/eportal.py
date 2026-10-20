#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锐捷 ePortal 认证接口客户端（无 UI 依赖，便于测试与复用）。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.39'
)

SENSITIVE_KEYS = ('password', 'operatorpwd', 'validcode', 'cookie')


@dataclass
class AuthResult:
    """认证接口调用结果。"""

    ok: bool
    result: Optional[str] = None
    message: str = ''
    status: Optional[int] = None
    raw: Any = None
    error: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志/调试
        if self.error:
            return '失败（%s）' % self.error
        return '%s: %s' % (self.result or 'unknown', self.message or '')


@dataclass
class RequestLog:
    """脱敏后的请求记录，便于排查问题且不泄露密码。"""

    url: str = ''
    fields: Dict[str, str] = field(default_factory=dict)

    def masked_fields(self) -> Dict[str, str]:
        masked: Dict[str, str] = {}
        for key in self.fields:
            masked[key] = '***' if key.lower() in SENSITIVE_KEYS else '<已填写>' if self.fields[key] else '<空>'
        return masked


class EportalClient:
    """封装 ePortal 的 login / logout 两个 POST 接口。"""

    def __init__(self, cfg: Dict[str, Any], *, timeout: Optional[float] = None, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.log = logger or log
        self.url = cfg['url']
        self.timeout = float(timeout or cfg.get('daemon', {}).get('login_timeout', 10) or 10)
        self.headers = self.build_headers()

    # ------------------------------------------------------------------ #
    # 请求构造
    # ------------------------------------------------------------------ #
    def build_headers(self) -> Dict[str, str]:
        server = str(self.url['server'])
        headers = {
            'Connection': 'keep-alive',
            'User-Agent': USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cookie': urllib.parse.quote(str(self.cfg.get('cookie') or '')),
            'Host': server.replace('http://', '').replace('https://', ''),
            'Origin': server,
        }
        for key, value in (self.cfg.get('headers') or {}).items():
            if value is None or str(value) == '':
                continue
            headers[str(key)] = str(value)
        return headers

    def _post(self, path: str, data: Optional[Dict[str, Any]]) -> AuthResult:
        url = str(self.url['server']).rstrip('/') + str(path)
        payload = dict(data or {})
        body = urllib.parse.urlencode(payload, doseq=True).encode('utf-8') if payload else None

        record = RequestLog(url=url, fields=payload)
        self.log.debug('POST %s 参数=%s', record.url, record.masked_fields())

        req = urllib.request.Request(url, data=body, headers=self.headers, method='POST')

        status: Optional[int] = None
        text = ''
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, 'status', None) or resp.getcode()
                text = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                text = exc.read().decode('utf-8', errors='replace')
            except Exception:
                text = ''
            self.log.warning('认证接口返回 HTTP %s：%s', status, url)
        except Exception as exc:
            message = '%s: %s' % (type(exc).__name__, exc)
            self.log.warning('认证请求失败：%s', message)
            return AuthResult(False, status=status, error=message)

        parsed: Any = None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            snippet = ' '.join(text.split())[:200]
            self.log.warning('认证接口返回的不是 JSON（HTTP %s）：%s', status, snippet or '<空响应>')

        if not isinstance(parsed, dict):
            return AuthResult(False, status=status, raw=text, error='认证接口返回内容无法解析为 JSON')

        result = parsed.get('result')
        message = str(parsed.get('message') or '')
        ok = result == 'success'
        if not ok:
            self.log.warning('认证未通过：result=%s message=%s', result, message or '<空>')
        return AuthResult(ok, result, message, status, parsed)

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def login(self) -> AuthResult:
        """执行登录（联网）认证。"""

        return self._post(self.url['login'], self.cfg.get('login_data') or {})

    def logout(self) -> AuthResult:
        """执行断网（下线）请求。

        上游实现是直接复用 ``login_data``；这里保留同样的兜底行为：
        配置文件里 ``logout_data`` 有内容时优先使用它。
        """

        data = self.cfg.get('logout_data') or self.cfg.get('login_data') or {}
        return self._post(self.url['logout'], data)
