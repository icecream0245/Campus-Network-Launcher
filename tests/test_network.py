#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络探测测试：对着本地 mock 服务器验证在线/离线判定。"""

from __future__ import annotations

import pytest

from campus_network_launcher import network
from tests.mock_eportal import MockEportalServer


@pytest.fixture
def server():
    with MockEportalServer() as srv:
        yield srv


def test_generate_204_online(server):
    server.state.online = True
    result = network.probe_once(server.base_url + '/generate_204', timeout=3)

    assert result.online is True
    assert result.status == 204


def test_generate_204_redirect_means_offline(server):
    server.state.online = False
    result = network.probe_once(server.base_url + '/generate_204', timeout=3)

    # 被网关重定向到认证页 => 未联网
    assert result.online is False
    assert result.status == 200


def test_connecttest_online(server):
    server.state.online = True
    result = network.probe_once(server.base_url + '/connecttest.txt', timeout=3)

    assert result.online is True
    assert result.status == 200


def test_unreachable_probe_reports_error():
    result = network.probe_once('http://127.0.0.1:1/generate_204', timeout=2)

    assert result.online is False
    assert result.error
    assert '失败' in result.detail


def test_is_online_falls_back_to_next_probe(server):
    server.state.online = True
    probes = ['http://127.0.0.1:1/generate_204', server.base_url + '/generate_204']
    result = network.is_online(probes, timeout=2)

    assert result.online is True
    assert result.url.endswith('/generate_204')


def test_is_online_reports_last_failure(server):
    server.state.online = False
    probes = [server.base_url + '/generate_204', server.base_url + '/connecttest.txt']
    result = network.is_online(probes, timeout=2)

    assert result.online is False
    assert result.url.endswith('/connecttest.txt')


def test_can_reach_server(server):
    assert network.can_reach(server.base_url, timeout=2) is True


def test_can_reach_unreachable_server():
    assert network.can_reach('http://127.0.0.1:1', timeout=2) is False


def test_can_reach_empty_server():
    assert network.can_reach('', timeout=2) is False


# --------------------------------------------------------------------------- #
# 内容校验型探测（防止被认证门户劫持后误判为在线）
# --------------------------------------------------------------------------- #
def test_probe_with_contains_keyword_online(server):
    server.state.online = True
    spec = {'url': server.base_url + '/connecttest.txt', 'contains': 'Microsoft Connect Test'}
    result = network.probe_once(spec, timeout=2)

    assert result.online is True
    assert result.status == 200


def test_probe_with_contains_rejects_hijacked_page(server):
    server.state.online = False  # 被重定向到认证页，最终返回 200
    spec = {'url': server.base_url + '/connecttest.txt', 'contains': 'Microsoft Connect Test'}
    result = network.probe_once(spec, timeout=2)

    assert result.online is False
    assert result.error is not None


def test_probe_expect_status_rule(server):
    server.state.online = True

    ok = network.probe_once({'url': server.base_url + '/connecttest.txt', 'expect_status': 200}, timeout=2)
    assert ok.online is True

    mismatch = network.probe_once({'url': server.base_url + '/connecttest.txt', 'expect_status': 204}, timeout=2)
    assert mismatch.online is False


def test_is_online_with_mixed_probe_rules(server):
    server.state.online = True
    probes = [
        {'url': 'http://127.0.0.1:1/generate_204'},
        {'url': server.base_url + '/connecttest.txt', 'contains': 'Microsoft Connect Test'},
    ]
    result = network.is_online(probes, timeout=2)

    assert result.online is True


def test_probe_spec_from_string_and_mapping():
    spec = network.ProbeSpec.from_value('http://a.example/generate_204')
    assert spec.url == 'http://a.example/generate_204'
    assert spec.expects_204 is True

    spec2 = network.ProbeSpec.from_value({'url': 'http://b.example/x', 'contains': 'ok', 'expect_status': [200, 204]})
    assert spec2.expect_status == (200, 204)
    assert spec2.contains == 'ok'
    assert spec2.expects_204 is False


def test_default_probe_specs_are_valid():
    specs = [network.ProbeSpec.from_value(item) for item in network.DEFAULT_PROBES]

    assert all(spec.url for spec in specs)
    assert any(spec.contains for spec in specs)
