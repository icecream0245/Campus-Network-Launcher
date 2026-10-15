#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守护循环状态机测试（注入假时钟与假客户端，不需要联网）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ruijie_eporta.daemon import EXIT_AUTH_FAILED, EXIT_OK, Daemon
from ruijie_eporta.eportal import AuthResult
from tests.conftest import FakeClient, FakeClock, RecordingNotifier, offline_probe, online_probe


def build_daemon(
    cfg,
    *,
    client=None,
    clock=None,
    probes: Optional[List] = None,
    reachable: bool = True,
    notifier=None,
    listener=None,
    stop_event=None,
    extra: Optional[Dict[str, Any]] = None,
):
    clock = clock or FakeClock()
    queue = list(probes or [])

    def online_probe_fn():
        if queue:
            return queue.pop(0)
        return online_probe()

    daemon = Daemon(
        cfg,
        client if client is not None else FakeClient([AuthResult(True, 'success', '')]),
        notifier=notifier or RecordingNotifier(),
        sleep=clock.sleep,
        now=clock.now,
        online_probe=online_probe_fn,
        reach_probe=lambda: reachable,
        ask_disconnect=lambda: True,
        listener=listener,
        stop_event=stop_event,
        **(extra or {}),
    )
    return daemon, clock


# --------------------------------------------------------------------------- #
# 退避策略
# --------------------------------------------------------------------------- #
def test_backoff_sequence_is_exponential_and_capped(config_factory):
    cfg = config_factory(extra='daemon:\n  retry_interval: 5\n  max_retry_interval: 20\n')
    daemon, _ = build_daemon(cfg)

    assert [daemon.backoff_delay(n) for n in range(1, 7)] == [5, 10, 20, 20, 20, 20]


def test_backoff_cooldown_after_failures(config_factory):
    cfg = config_factory(
        extra='daemon:\n  retry_interval: 5\n  max_retry_interval: 20\n  cooldown_after_failures: 3\n  cooldown: 120\n'
    )
    daemon, _ = build_daemon(cfg)

    assert daemon.backoff_delay(1) == 5
    assert daemon.backoff_delay(2) == 10
    assert daemon.backoff_delay(3) == 120
    assert daemon.backoff_delay(10) == 120


# --------------------------------------------------------------------------- #
# 守护循环
# --------------------------------------------------------------------------- #
def test_reconnect_success_flow(config_factory):
    cfg = config_factory(extra='daemon:\n  check_interval: 30\n')
    clock = FakeClock()
    notifier = RecordingNotifier()
    client = FakeClient([AuthResult(True, 'success', '')])
    daemon, clock = build_daemon(
        cfg,
        client=client,
        clock=clock,
        notifier=notifier,
        probes=[offline_probe(), online_probe()],
    )

    assert daemon.run(max_iterations=2) == EXIT_OK
    assert client.login_calls == 1
    assert '联网成功' in notifier.titles()
    # 第一次循环：重连后等待检测间隔；第二次循环：正常等待
    assert clock.sleeps == [30.0, 30.0]


def test_failed_reconnect_uses_backoff_and_throttles_notifications(config_factory):
    cfg = config_factory(extra='daemon:\n  check_interval: 30\n  retry_interval: 5\n  max_retry_interval: 40\n')
    clock = FakeClock()
    notifier = RecordingNotifier()
    client = FakeClient([])  # 始终返回失败
    daemon, clock = build_daemon(
        cfg,
        client=client,
        clock=clock,
        notifier=notifier,
        probes=[offline_probe() for _ in range(10)],
    )

    daemon.run(max_iterations=6)

    assert client.login_calls == 6
    assert daemon.failures == 6
    # 失败等待时间按 5/10/20/40/40/40 递增
    assert clock.sleeps == [5.0, 10.0, 20.0, 40.0, 40.0, 40.0]
    # 只在第 1 次和第 5 次失败时通知，避免通知轰炸
    assert notifier.titles('warn') == ['联网失败', '联网失败']


def test_recovery_notification_after_failures(config_factory):
    cfg = config_factory(extra='daemon:\n  retry_interval: 1\n  max_retry_interval: 2\n')
    clock = FakeClock()
    notifier = RecordingNotifier()
    client = FakeClient([AuthResult(False, 'fail', '密码错误', error='密码错误')])
    daemon, _ = build_daemon(
        cfg,
        client=client,
        clock=clock,
        notifier=notifier,
        probes=[offline_probe(), online_probe(), online_probe()],
    )

    daemon.run(max_iterations=3)
    assert daemon.failures == 0
    assert '网络已恢复' in notifier.titles()


def test_skip_login_when_not_in_campus_network(config_factory):
    cfg = config_factory(extra='daemon:\n  check_interval: 12\n')
    clock = FakeClock()
    client = FakeClient([AuthResult(True, 'success', '')])
    daemon, clock = build_daemon(
        cfg,
        client=client,
        clock=clock,
        reachable=False,
        probes=[offline_probe() for _ in range(4)],
    )

    daemon.run(max_iterations=3)
    # 不在校园网环境时不应发起认证，只是按检测间隔重试
    assert client.login_calls == 0
    assert clock.sleeps == [12.0, 12.0, 12.0]


def test_keepalive_probe_runs_periodically(config_factory):
    cfg = config_factory(extra='daemon:\n  check_interval: 30\n  keepalive_interval: 60\n')
    clock = FakeClock()
    calls: List[float] = []

    def online_probe_fn():
        calls.append(clock.now())
        return online_probe()

    daemon = Daemon(
        cfg,
        FakeClient(),
        notifier=RecordingNotifier(),
        sleep=clock.sleep,
        now=clock.now,
        online_probe=online_probe_fn,
        reach_probe=lambda: True,
    )

    daemon.run(max_iterations=6)
    # 6 次主循环 + 在 t=60、t=120 各触发一次保活探测
    assert len(calls) == 8


def test_signal_stop_breaks_loop(config_factory):
    cfg = config_factory()
    clock = FakeClock()
    daemon, clock = build_daemon(cfg, clock=clock, probes=[online_probe()])

    def stop_after_first(_seconds):
        clock.time += _seconds
        daemon.request_stop()

    daemon._sleep = stop_after_first  # noqa: SLF001 - 测试内部行为
    daemon.run()
    assert daemon.stopping is True


# --------------------------------------------------------------------------- #
# 事件回调与可中断停止（可视化界面依赖这两项）
# --------------------------------------------------------------------------- #
def test_listener_receives_lifecycle_events(config_factory):
    cfg = config_factory(extra='daemon:\n  check_interval: 5\n')
    events: List[str] = []
    client = FakeClient([AuthResult(True, 'success', '')])
    daemon, _ = build_daemon(
        cfg,
        client=client,
        probes=[offline_probe(), online_probe(), online_probe()],
        listener=lambda event, payload: events.append(event),
    )

    daemon.run(max_iterations=2)

    assert events[0] == 'watch_started'
    assert 'login_ok' in events
    assert 'state' in events
    assert events[-1] == 'watch_stopped'


def test_listener_exception_does_not_break_watch(config_factory):
    cfg = config_factory()
    client = FakeClient([AuthResult(True, 'success', '')])

    def broken_listener(_event, _payload):
        raise RuntimeError('监听者异常')

    daemon, _ = build_daemon(cfg, client=client, probes=[online_probe()], listener=broken_listener)
    assert daemon.run(max_iterations=1) == EXIT_OK


def test_stop_event_interrupts_long_wait(config_factory):
    import threading

    cfg = config_factory(extra='daemon:\n  check_interval: 3600\n')
    stop_event = threading.Event()
    daemon = Daemon(
        cfg,
        FakeClient(),
        notifier=RecordingNotifier(),
        online_probe=lambda: online_probe(),
        reach_probe=lambda: True,
        stop_event=stop_event,
    )

    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    time.sleep(0.2)
    daemon.request_stop()
    thread.join(timeout=3)

    assert thread.is_alive() is False


# --------------------------------------------------------------------------- #
# 单次模式（兼容上游行为）
# --------------------------------------------------------------------------- #
def test_run_once_logs_in_when_offline(config_factory):
    cfg = config_factory()
    client = FakeClient([AuthResult(True, 'success', '')])
    daemon, _ = build_daemon(cfg, client=client, probes=[offline_probe()])

    assert daemon.run_once() == EXIT_OK
    assert client.login_calls == 1
    assert client.logout_calls == 0


def test_run_once_reports_login_failure(config_factory):
    cfg = config_factory()
    notifier = RecordingNotifier()
    client = FakeClient([AuthResult(False, 'fail', '账号被锁定', error='账号被锁定')])
    daemon, _ = build_daemon(cfg, client=client, notifier=notifier, probes=[offline_probe()])

    assert daemon.run_once() == EXIT_AUTH_FAILED
    assert notifier.titles('error') == ['联网失败']


def test_run_once_disconnects_when_asked(config_factory):
    cfg = config_factory()
    client = FakeClient()
    daemon, _ = build_daemon(cfg, client=client, probes=[online_probe()])

    assert daemon.run_once(auto_disconnect=True) == EXIT_OK
    assert client.logout_calls == 1


def test_run_once_keeps_connection_when_user_declines(config_factory):
    cfg = config_factory()
    client = FakeClient()
    daemon, _ = build_daemon(cfg, client=client, probes=[online_probe()])

    assert daemon.run_once(auto_disconnect=False) == EXIT_OK
    assert client.logout_calls == 0


def test_run_once_disconnect_disabled(config_factory):
    cfg = config_factory(disconnect_network='false')
    notifier = RecordingNotifier()
    client = FakeClient()
    daemon, _ = build_daemon(cfg, client=client, notifier=notifier, probes=[online_probe()])

    assert daemon.run_once() == EXIT_OK
    assert client.logout_calls == 0
    assert client.login_calls == 0
    assert '设备已联网' in notifier.titles()


def test_run_once_not_in_campus_network_exits(config_factory):
    cfg = config_factory()
    clock = FakeClock()
    notifier = RecordingNotifier()
    client = FakeClient()
    daemon, clock = build_daemon(cfg, client=client, notifier=notifier, clock=clock, reachable=False)

    assert daemon.run_once() == EXIT_OK
    assert client.login_calls == 0
    assert clock.sleeps == [10.0]
    assert notifier.titles('warn') == ['非校园网环境']


# --------------------------------------------------------------------------- #
# 状态查询
# --------------------------------------------------------------------------- #
def test_status_contains_expected_fields(config_factory):
    cfg = config_factory(server='http://10.1.2.3')
    daemon, _ = build_daemon(cfg, probes=[online_probe()], reachable=True)

    info = daemon.status()
    assert info['server'] == 'http://10.1.2.3'
    assert info['user_id'] == '2023000001'
    assert info['online'] is True
    assert info['school_network'] is True
    assert info['config_path'].endswith('config.yml')
