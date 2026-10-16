#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟锐捷 ePortal 认证门户，用于验证「免抓包」流程（离线、可直接断言）。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

DEFAULT_SERVICES = {
    'default': {'serviceName': 'default', 'serviceShowName': '教师免费', 'serviceDefault': 'true'},
    '中国电信': {'serviceName': '中国电信', 'serviceShowName': '中国电信（宿舍）', 'serviceDefault': 'false'},
    '中国移动': {'serviceName': '中国移动', 'serviceShowName': '中国移动（宿舍）', 'serviceDefault': 'false'},
}


class MockPortalState:
    """模拟服务器的可变状态。"""

    def __init__(self) -> None:
        self.online = False
        self.user_id = '2023000001'
        self.password = 'secret123'
        self.mac = 'aabbccddeeff00112233445566778899'
        self.wlanuserip = 'deadbeefdeadbeefdeadbeefdeadbeef'
        self.nasip = '00112233445566778899aabbccddeeff'
        self.modulus = 'c' * 256
        self.exponent = '10001'
        self.password_encrypt = True
        self.services: Dict[str, Any] = dict(DEFAULT_SERVICES)
        self.anonymous_page = False
        self.expect_password: Optional[str] = None
        self.login_calls = 0
        self.page_info_calls = 0
        self.portal_calls = 0
        self.last_login_form: Dict[str, Any] = {}
        self.last_page_info_form: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def portal_query(self) -> str:
        return (
            'wlanuserip=%s&wlanacname=9fb68da17b0bec1b0b1ff19b5c42f88c&ssid=&nasip=%s'
            '&snmpagentip=&mac=%s&t=wireless-v2&url=e32cf7308cc6136f4b129764bc9e47af'
            '&apmac=&nasid=9fb68da17b0bec1b0b1ff19b5c42f88c&vid=50e295aa4b584845'
            '&port=c164c6d0da85f634&nasportid=7b050a0f6528ba3db332c492ac0a6b6b'
            % (self.wlanuserip, self.nasip, self.mac)
        )

    def portal_url(self, base_url: str) -> str:
        return '%s/eportal/index.jsp?%s' % (base_url, self.portal_query())


class MockPortalHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'MockPortal/1.0'

    def log_message(self, *args: Any) -> None:  # pragma: no cover
        pass

    @property
    def state(self) -> MockPortalState:
        return self.server.state  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    def _send(self, status: int, body: bytes = b'', content_type: str = 'text/html; charset=utf-8', **headers: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, value in headers.items():
            self.send_header(key.replace('_', '-'), value)
        self.end_headers()
        if body and self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path.rstrip('/').endswith('generate_204'):
            if self.state.online:
                self._send(204)
            else:
                self._send(302, b'', Location=self.state.portal_url(self.server.base_url))  # type: ignore[attr-defined]
        elif path.startswith('/eportal/index.jsp') or path == '/eportal/':
            with self.state.lock:
                self.state.portal_calls += 1
                online = self.state.online
            if online and self.state.anonymous_page:
                self._send(200, b'<html><body>already online</body></html>')
                return
            html = (
                '<html><head><meta charset="utf-8"></head><body>'
                '<select id="service"><option value="中国电信">中国电信（宿舍）</option></select>'
                '<input id="publicKeyModulus" value="">'
                '</body></html>'
            )
            self._send(200, html.encode('utf-8'))
        else:
            self._send(404, b'not found')

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8', errors='replace')
        form = {key: values[0] for key, values in urllib.parse.parse_qs(body, keep_blank_values=True).items()}

        if not parsed.path.endswith('/eportal/InterFace.do'):
            self._json({'result': 'fail', 'message': '未知接口'}, status=404)
            return

        if 'method=pageInfo' in parsed.query:
            with self.state.lock:
                self.state.page_info_calls += 1
                self.state.last_page_info_form = form
                state = self.state
            self._json(
                {
                    'passwordEncrypt': 'true' if state.password_encrypt else 'false',
                    'publicKeyExponent': state.exponent,
                    'publicKeyModulus': state.modulus,
                    'service': state.services,
                    'validCodeUrl': '',
                    'successPage': 'success.jsp',
                }
            )
            return

        if 'method=login' in parsed.query:
            with self.state.lock:
                self.state.login_calls += 1
                self.state.last_login_form = form
                expect = self.state.expect_password
                user_id = self.state.user_id
            if expect is not None and form.get('password') != expect:
                self._json({'result': 'fail', 'message': '密码密文不匹配'})
            elif form.get('userId') != user_id:
                self._json({'result': 'fail', 'message': '账号不存在'})
            else:
                with self.state.lock:
                    self.state.online = True
                self._json({'result': 'success', 'message': '', 'userIndex': '1234567890'})
            return

        self._json({'result': 'fail', 'message': '未知 method'}, status=404)


class MockPortalServer:
    """上下文管理器：``with MockPortalServer() as srv: srv.base_url``。"""

    def __init__(self) -> None:
        self.state = MockPortalState()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), MockPortalHandler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.httpd.daemon_threads = True
        host, port = self.httpd.server_address[:2]
        self.base_url = 'http://%s:%d' % (host, port)
        self.httpd.base_url = self.base_url  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)

    def start(self) -> 'MockPortalServer':
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> 'MockPortalServer':
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()
