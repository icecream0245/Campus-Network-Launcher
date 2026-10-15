#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证客户端测试：全部对着本地 mock ePortal 服务器跑，无需真实校园网。"""

from __future__ import annotations

import pytest

from ruijie_eporta.eportal import EportalClient
from tests.mock_eportal import MockEportalServer


@pytest.fixture
def server():
    with MockEportalServer() as srv:
        yield srv


def make_client(config_factory, server, **kwargs):
    cfg = config_factory(server=server.base_url, **kwargs)
    return EportalClient(cfg), cfg


def test_login_success(config_factory, server):
    client, _ = make_client(config_factory, server)
    result = client.login()

    assert result.ok is True
    assert result.result == 'success'
    assert server.state.login_calls == 1
    assert server.state.online is True


def test_login_sends_expected_form_fields(config_factory, server):
    client, _ = make_client(config_factory, server)
    client.login()

    params = server.state.last_login_params
    assert params['userId'] == '2023000001'
    assert params['password'] == '0123456789abcdef'
    assert params['passwordEncrypt'] == 'true'
    assert params['queryString'] == 'wlanuserip%3D1%26wlanacname%3D2'


def test_login_sends_expected_headers(config_factory, server):
    client, _ = make_client(config_factory, server)
    client.login()

    headers = {k.lower(): v for k, v in server.state.last_headers.items()}
    assert headers['host'] == '127.0.0.1:%d' % server.port
    assert headers['origin'] == server.base_url
    assert 'EPORTAL_COOKIE_USERNAME' in headers['cookie']
    assert headers['content-type'].startswith('application/x-www-form-urlencoded')
    assert headers['referer'].endswith('/eportal/index.jsp')


def test_login_failure_reported(config_factory, server):
    server.state.login_should_fail = True
    server.state.fail_message = '密码错误'
    client, _ = make_client(config_factory, server)
    result = client.login()

    assert result.ok is False
    assert result.result == 'fail'
    assert result.message == '密码错误'
    assert server.state.online is False


def test_login_non_json_response(config_factory, server):
    server.state.raw_login_response = '<html><body>502 Bad Gateway</body></html>'
    client, _ = make_client(config_factory, server)
    result = client.login()

    assert result.ok is False
    assert result.error is not None
    assert 'JSON' in result.error


def test_login_malformed_json(config_factory, server):
    server.state.raw_login_response = '{"result": "success"'
    client, _ = make_client(config_factory, server)
    result = client.login()

    assert result.ok is False
    assert result.error is not None


def test_login_http_error_status(config_factory, server):
    server.state.login_status = 500
    server.state.raw_login_response = '{}'
    client, _ = make_client(config_factory, server)
    result = client.login()

    assert result.ok is False
    assert result.status == 500


def test_login_connection_error(config_factory):
    # 指向一个不会被监听的端口
    cfg = config_factory(server='http://127.0.0.1:1')
    client = EportalClient(cfg, timeout=2)
    result = client.login()

    assert result.ok is False
    assert result.error


def test_logout_uses_login_data_by_default(config_factory, server):
    server.state.online = True
    client, _ = make_client(config_factory, server)
    result = client.logout()

    assert result.ok is True
    assert server.state.logout_calls == 1
    assert server.state.last_logout_params['userId'] == '2023000001'
    assert server.state.online is False


def test_logout_prefers_logout_data(config_factory, server):
    cfg = config_factory(server=server.base_url)
    cfg['logout_data'] = {'userId': '9999', 'custom': 'yes'}
    client = EportalClient(cfg)
    client.logout()

    assert server.state.last_logout_params['userId'] == '9999'
    assert server.state.last_logout_params['custom'] == 'yes'


def test_timeout_setting_from_config(config_factory, server):
    cfg = config_factory(server=server.base_url, extra='daemon:\n  login_timeout: 3\n')
    client = EportalClient(cfg)
    assert client.timeout == 3.0
