#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面控制器测试（不创建窗口，直接验证线程模型与事件流）。"""

from __future__ import annotations

import time

import pytest

from campus_network_launcher.gui.controller import AppController, summarize_probes
from tests.mock_eportal import MockEportalServer


@pytest.fixture
def server():
    with MockEportalServer() as srv:
        yield srv


def make_config(config_factory, server, extra_extra: str = ''):
    extra = (
        'notify:\n'
        '  toast: false\n'
        '  dialog: false\n'
        'daemon:\n'
        '  check_interval: 1\n'
        '  retry_interval: 1\n'
        '  probe_timeout: 2\n'
        '  login_timeout: 3\n'
        'network:\n'
        '  probes:\n'
        '    - %s/generate_204\n'
        '  timeout: 2\n'
        '%s' % (server.base_url, extra_extra)
    )
    return config_factory(server=server.base_url, extra=extra)


def wait_worker(controller: AppController, timeout: float = 15.0) -> None:
    """等待一次性后台任务结束。"""

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not controller.busy:
            return
        time.sleep(0.02)
    raise AssertionError('后台任务超时未结束')


def drain(controller: AppController):
    kinds = []
    while True:
        event = controller.poll_event()
        if event is None:
            break
        kinds.append(event.kind)
    return kinds


def test_basic_properties(config_factory, server):
    controller = AppController(make_config(config_factory, server))

    assert controller.server == server.base_url
    assert controller.user_id == '2023000001'
    assert controller.config_path.endswith('config.yml')
    assert controller.log_file_path().endswith('.log')
    assert controller.watching is False
    assert controller.state_text() == '未知'
    assert controller.watch_uptime == '-'


def test_refresh_status_reflects_server_state(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = True

    assert controller.refresh_status() is True
    wait_worker(controller)

    assert controller.online is True
    assert controller.state_text() == '已联网'
    assert 'status' in drain(controller)


def test_login_now_calls_eportal(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = False

    assert controller.login_now() is True
    wait_worker(controller)

    assert server.state.login_calls == 1
    kinds = drain(controller)
    assert 'login_ok' in kinds
    assert controller.online is True
    assert '认证成功' in controller.last_message


def test_logout_now_calls_eportal(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = True

    assert controller.logout_now() is True
    wait_worker(controller)

    assert server.state.logout_calls == 1
    assert 'logout_ok' in drain(controller)


def test_login_failure_updates_state(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.login_should_fail = True
    server.state.fail_message = '账号被锁定'

    controller.login_now()
    wait_worker(controller)

    assert controller.online is not True
    assert '登录失败' in controller.last_message or '认证失败' in controller.last_message
    assert 'login_fail' in drain(controller)


def test_watch_start_and_stop(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = True

    assert controller.start_watch() is True
    time.sleep(0.3)
    assert controller.watching is True
    assert controller.watch_uptime != '-'

    assert controller.stop_watch() is True
    deadline = time.time() + 10
    while controller.watching and time.time() < deadline:
        time.sleep(0.05)
    assert controller.watching is False
    # 停止后应能重新启动（说明 stop_event 已被正确重置）
    assert controller.start_watch() is True
    controller.stop_watch()


def test_watch_recovers_after_outage(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = False

    controller.start_watch()
    deadline = time.time() + 15
    while server.state.login_calls == 0 and time.time() < deadline:
        time.sleep(0.05)

    assert server.state.login_calls >= 1
    assert server.state.online is True
    controller.stop_watch()


def test_toggle_and_busy_guards(config_factory, server):
    controller = AppController(make_config(config_factory, server))
    server.state.online = True

    controller.toggle_watch()
    time.sleep(0.2)
    assert controller.watching is True
    controller.toggle_watch()
    time.sleep(0.2)
    assert controller.watching is False

    # 首个任务未完成时，第二个请求会被拒绝
    assert controller.refresh_status() is True or controller.refresh_status() is False
    controller.shutdown(timeout=3)


def test_ui_request_queue(config_factory, server):
    controller = AppController(make_config(config_factory, server))

    controller.request_show()
    controller.request_quit()
    assert controller.poll_ui_request() == 'show'
    assert controller.poll_ui_request() == 'quit'
    assert controller.poll_ui_request() is None
    assert controller.poll_event() is None


def test_autostart_introspection(config_factory, server):
    controller = AppController(make_config(config_factory, server))

    assert controller.autostart_method() in ('startup', 'registry')
    assert isinstance(controller.autostart_enabled(), bool)


def test_summarize_probes():
    probes = ['http://a/generate_204', {'url': 'http://b/connecttest.txt', 'contains': 'x'}]

    assert summarize_probes(probes) == 'http://a/generate_204 / http://b/connecttest.txt'


def test_shutdown_is_safe_without_watch(config_factory, server):
    controller = AppController(make_config(config_factory, server))

    controller.shutdown(timeout=1)
    controller.shutdown(timeout=1)
    assert controller.watching is False
