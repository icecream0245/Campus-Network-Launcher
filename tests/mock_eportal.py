#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地模拟 ePortal 服务器，用于离线验证（不需要真实校园网）。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional


class MockState:
    """模拟服务器的可变状态，测试中直接修改以模拟网络变化。"""

    def __init__(self) -> None:
        self.online = False
        self.login_should_fail = False
        self.fail_message = '认证失败：密码错误'
        self.login_message = ''
        self.raw_login_response: Optional[str] = None
        self.login_status = 200
        self.login_calls = 0
        self.logout_calls = 0
        self.last_login_params: Dict[str, Any] = {}
        self.last_logout_params: Dict[str, Any] = {}
        self.last_headers: Dict[str, str] = {}
        self.last_login_body = ''
        self.lock = threading.Lock()


class MockEportalHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'MockEportal/1.0'

    # 静默访问日志
    def log_message(self, *args: Any) -> None:  # pragma: no cover
        pass

    # ------------------------------------------------------------------ #
    @property
    def state(self) -> MockState:
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes = b'', content_type: str = 'text/plain; charset=utf-8', **headers: str) -> None:
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
        path = urllib.parse.urlparse(self.path).path
        if path.rstrip('/').endswith('generate_204'):
            if self.state.online:
                self._send(204)
            else:
                self._send(302, b'', Location='/eportal/index.jsp')
        elif path.endswith('connecttest.txt'):
            if self.state.online:
                self._send(200, b'Microsoft Connect Test')
            else:
                self._send(302, b'', Location='/eportal/index.jsp')
        elif path.startswith('/eportal'):
            self._send(200, b'<html><body>eportal</body></html>', 'text/html; charset=utf-8')
        else:
            self._send(404, b'not found')

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8', errors='replace')
        params = {k: (v[0] if len(v) == 1 else v) for k, v in urllib.parse.parse_qs(body).items()}
        query = urllib.parse.urlparse(self.path).query

        with self.state.lock:
            self.state.last_headers = {k: v for k, v in self.headers.items()}
            self.state.last_login_body = body

        if 'method=login' in query:
            with self.state.lock:
                self.state.login_calls += 1
                self.state.last_login_params = params
                should_fail = self.state.login_should_fail
                raw = self.state.raw_login_response
                status = self.state.login_status
                message = self.state.fail_message
                success_message = self.state.login_message
                if not should_fail:
                    self.state.online = True
            if raw is not None:
                self._send(status, raw.encode('utf-8'), 'text/html; charset=utf-8')
            elif should_fail:
                self._json({'result': 'fail', 'message': message}, status=status)
            else:
                self._json({'result': 'success', 'message': success_message}, status=status)
        elif 'method=logout' in query:
            with self.state.lock:
                self.state.logout_calls += 1
                self.state.last_logout_params = params
                self.state.online = False
            self._json({'result': 'success', 'message': ''})
        else:
            self._json({'result': 'fail', 'message': '未知 method'}, status=404)


class MockEportalServer:
    """上下文管理器：``with MockEportalServer() as srv: srv.base_url``。"""

    def __init__(self) -> None:
        self.state = MockState()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), MockEportalHandler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return 'http://%s:%d' % (host, port)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> 'MockEportalServer':
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> 'MockEportalServer':
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()
