#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 公共 fixtures 与工具函数。"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ruijie_eporta import config  # noqa: E402
from ruijie_eporta.eportal import AuthResult  # noqa: E402
from ruijie_eporta.network import ProbeResult  # noqa: E402

# 与用户正在使用的 v3 配置结构一致（值已脱敏）
BASE_CONFIG_TEMPLATE = '''\
main:
  version: 3

funtion:
  check_school_network: {check_school_network}
  disconnect_network: {disconnect_network}

url:
  server: {server}
  login: /eportal/InterFace.do?method=login
  logout: /eportal/InterFace.do?method=logout

cookie: 'EPORTAL_COOKIE_USERNAME={user_id}; JSESSIONID=TEST'

login_data:
  userId: '{user_id}'
  password: '0123456789abcdef'
  service: '%E4%B8%AD%E5%9B%BD%E7%94%B5%E4%BF%A1'
  queryString: 'wlanuserip%3D1%26wlanacname%3D2'
  operatorPwd:
  operatorUserId:
  validcode:
  passwordEncrypt: true

logout_data:

headers:
  Referer: {server}/eportal/index.jsp
{extra}
'''


def write_config_file(
    directory: str,
    *,
    server: str = 'http://127.0.0.1:9',
    user_id: str = '2023000001',
    check_school_network: str = 'true',
    disconnect_network: str = 'true',
    extra: str = '',
    filename: str = 'config.yml',
) -> str:
    """按上游 v3 结构写出一份配置文件，返回路径。"""

    text = BASE_CONFIG_TEMPLATE.format(
        server=server,
        user_id=user_id,
        check_school_network=check_school_network,
        disconnect_network=disconnect_network,
        extra=extra,
    )
    path = os.path.join(directory, filename)
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(text)
    return path


@pytest.fixture
def config_factory(tmp_path):
    """返回一个工厂函数：写配置并加载成 cfg 字典。"""

    def _make(**kwargs: Any) -> Dict[str, Any]:
        path = write_config_file(str(tmp_path), **kwargs)
        return config.load_config(path)

    return _make


@pytest.fixture
def config_path_factory(tmp_path):
    """返回一个工厂函数：只写配置文件，返回路径（不加载）。"""

    def _make(**kwargs: Any) -> str:
        return write_config_file(str(tmp_path), **kwargs)

    return _make


class FakeClock:
    """可注入的假时钟，sleep 不会真的等待。"""

    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: List[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.time += float(seconds)

    def now(self) -> float:
        return self.time


class FakeClient:
    """记录调用的假认证客户端。"""

    def __init__(self, login_results: Optional[List[AuthResult]] = None, logout_result: Optional[AuthResult] = None) -> None:
        self.login_results = list(login_results or [])
        self.logout_result = logout_result or AuthResult(True, 'success', '')
        self.login_calls = 0
        self.logout_calls = 0

    def login(self) -> AuthResult:
        self.login_calls += 1
        if self.login_results:
            return self.login_results.pop(0)
        return AuthResult(False, 'fail', '模拟失败', error='模拟失败')

    def logout(self) -> AuthResult:
        self.logout_calls += 1
        return self.logout_result


class RecordingNotifier:
    """记录通知内容，便于断言（不会真的弹窗）。"""

    def __init__(self) -> None:
        self.events: List[Tuple[str, str, str]] = []

    def info(self, title: str, message: str = '') -> None:
        self.events.append(('info', title, message))

    def warn(self, title: str, message: str = '') -> None:
        self.events.append(('warn', title, message))

    def error(self, title: str, message: str = '') -> None:
        self.events.append(('error', title, message))

    def titles(self, level: Optional[str] = None) -> List[str]:
        return [title for lvl, title, _ in self.events if level is None or lvl == level]


def online_probe(url: str = 'mock://probe') -> ProbeResult:
    return ProbeResult(True, url, 204, None)


def offline_probe(url: str = 'mock://probe', error: str = '模拟未联网') -> ProbeResult:
    return ProbeResult(False, url, None, error)
