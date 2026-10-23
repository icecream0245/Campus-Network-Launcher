#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次运行向导 / 服务商选择 / 配置写回 的测试（全部离线，不连真实校园网）。"""

from __future__ import annotations

import copy
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from campus_network_launcher import config, winutil
from campus_network_launcher.daemon import Daemon
from campus_network_launcher.eportal import AuthResult
from campus_network_launcher.gui.controller import AppController
from campus_network_launcher.portal import fetch_service_options, is_service_error
from tests.conftest import FakeClient, FakeClock, RecordingNotifier, offline_probe, online_probe
from tests.mock_portal import MockPortalServer


@pytest.fixture
def portal():
    with MockPortalServer() as server:
        yield server


def minimal_cfg(tmp_path: Path, **auto_overrides) -> dict:
    cfg = {
        'main': {'version': 3},
        'funtion': {'check_school_network': True, 'disconnect_network': True},
        'url': {'server': 'http://127.0.0.1:9', 'login': '/eportal/InterFace.do?method=login'},
        'cookie': '',
        'login_data': {'userId': '', 'queryString': ''},
        'logout_data': {},
        'headers': {},
        '_config_path': str(tmp_path / 'config.yml'),
    }
    for key, value in config.DEFAULT_OPTIONS.items():
        cfg.setdefault(key, copy.deepcopy(value))
    cfg['auto'] = {'enabled': False, 'user_id': '', 'password': '', 'service': '', 'service_options': []}
    cfg['auto'].update(auto_overrides)
    return cfg


# --------------------------------------------------------------------------- #
# 是否"未配置"
# --------------------------------------------------------------------------- #
def test_is_profile_incomplete_cases():
    assert config.is_profile_incomplete({'login_data': {}, 'auto': {}}) is True
    assert config.is_profile_incomplete({'login_data': {'userId': '00000000000'}, 'auto': {}}) is True
    # 只有学号、没有密码 → 仍算未完成
    assert config.is_profile_incomplete({'login_data': {'userId': '2023000001'}, 'auto': {}}) is True
    # 免抓包账密齐全 → 已配置
    assert config.is_profile_incomplete(
        {'login_data': {}, 'auto': {'user_id': '2023000001', 'password': 'pw'}}
    ) is False
    # 有抓包参数 → 已配置（老用户的老配置）
    assert config.is_profile_incomplete(
        {'login_data': {'userId': '2023000001', 'queryString': 'wlanuserip%3Dx'}, 'auto': {}}
    ) is False


def test_build_profile_fields():
    fields = config.build_profile_fields(' 2023000001 ', 'secret', '中国电信', ['中国电信', '', '中国移动'])

    assert fields['enabled'] is True
    assert fields['user_id'] == '2023000001'
    assert fields['password'] == 'secret'
    assert fields['service'] == '中国电信'
    assert fields['service_options'] == ['中国电信', '中国移动']


# --------------------------------------------------------------------------- #
# 局部写回 auto 段
# --------------------------------------------------------------------------- #
def test_update_auto_fields_replaces_and_keeps_comments(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text(
        'main:\n  version: 3\n'
        '# 需要保留的注释\n'
        'auto:\n  enabled: false\n  user_id: ""\n  password: ""\n  service: ""\n'
        '# 尾部注释\nother: 1\n',
        encoding='utf-8',
    )

    _target, backup = config.update_auto_fields(
        str(path),
        {'enabled': True, 'user_id': '2023000001', 'password': 'secret', 'service': '中国电信', 'service_options': ['中国电信', '中国移动']},
    )

    text = path.read_text(encoding='utf-8')
    assert '# 需要保留的注释' in text
    assert '# 尾部注释' in text
    data = yaml.safe_load(text)
    assert data['auto']['enabled'] is True
    assert data['auto']['user_id'] == '2023000001'
    assert data['auto']['password'].startswith('dpapi:')
    assert winutil.dpapi_unprotect(data['auto']['password']) == 'secret'
    assert data['auto']['service'] == '中国电信'
    assert data['auto']['service_options'] == ['中国电信', '中国移动']
    assert data['other'] == 1  # 其它段不受影响
    assert backup and Path(backup).is_file()


def test_update_auto_fields_appends_missing_keys(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\nauto:\n  user_id: "x"\n', encoding='utf-8')

    config.update_auto_fields(str(path), {'service': '中国移动'})

    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert data['auto']['user_id'] == 'x'
    assert data['auto']['service'] == '中国移动'


def test_update_auto_fields_creates_section_when_absent(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\n', encoding='utf-8')

    config.update_auto_fields(str(path), {'user_id': 'u', 'password': 'p', 'enabled': True})

    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert data['auto']['enabled'] is True
    assert data['auto']['user_id'] == 'u'
    assert data['auto']['password'].startswith('dpapi:')
    assert winutil.dpapi_unprotect(data['auto']['password']) == 'p'


def test_update_auto_fields_result_is_loadable(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\nauto:\n  enabled: false\n', encoding='utf-8')

    config.update_auto_fields(str(path), {'enabled': True, 'user_id': '2023000001', 'password': 'pw'})

    loaded = config.load_config(str(path))
    assert loaded['auto']['enabled'] is True
    assert loaded['auto']['user_id'] == '2023000001'
    assert loaded['auto']['password'] == 'pw'


def test_update_auto_fields_requires_existing_file(tmp_path):
    with pytest.raises(config.ConfigError):
        config.update_auto_fields(str(tmp_path / 'nope.yml'), {'user_id': 'x'})


def test_update_auto_fields_rejects_unknown_keys(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\n', encoding='utf-8')

    with pytest.raises(config.ConfigError):
        config.update_auto_fields(str(path), {'nonsense': 1})


def test_load_config_allow_incomplete_marks_flag(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text(
        'main:\n  version: 3\n'
        'url:\n  server: http://10.0.0.1\n  login: /eportal/x\n'
        'login_data:\n  userId: "00000000000"\n',
        encoding='utf-8',
    )

    cfg = config.load_config(str(path), allow_incomplete=True)

    assert cfg['_needs_profile'] is True
    # 严格模式仍然应该报错
    with pytest.raises(config.ProfileIncompleteError):
        config.load_config(str(path))


# --------------------------------------------------------------------------- #
# 服务商错误判定与列表抓取
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    'message',
    ['该服务商不允许此用户', '运营商不匹配', '套餐错误', 'service not allowed', '不支持该服务'],
)
def test_is_service_error_hits(message):
    assert is_service_error(message) is True


@pytest.mark.parametrize('message', ['服务器错误', '密码错误', '账号被锁定', '', None])
def test_is_service_error_misses(message):
    assert is_service_error(message) is False


def test_fetch_service_options(portal):
    portal.state.online = False
    cfg = {'network': {'probes': [portal.base_url + '/generate_204'], 'timeout': 3}}

    services, error = fetch_service_options(cfg)

    assert error == ''
    assert services[0] == '教师免费'  # 默认项排在最前（显示名）
    assert '中国电信（宿舍）' in services
    assert '中国移动（宿舍）' in services


def test_fetch_service_options_when_online(portal):
    portal.state.online = True
    cfg = {'network': {'probes': [portal.base_url + '/generate_204'], 'timeout': 3}}

    services, error = fetch_service_options(cfg)

    assert services == []
    assert '已在线' in error


def test_fetch_service_options_when_unreachable():
    cfg = {'network': {'probes': ['http://127.0.0.1:1/generate_204'], 'timeout': 2}}

    services, error = fetch_service_options(cfg, timeout=2)

    assert services == []
    assert error


# --------------------------------------------------------------------------- #
# 守护进程：服务商拒绝事件
# --------------------------------------------------------------------------- #
def test_daemon_emits_service_rejected_only_once(config_factory):
    cfg = config_factory(extra='daemon:\n  retry_interval: 1\n  max_retry_interval: 2\n')
    events = []
    failing = AuthResult(False, 'fail', '该服务商不允许此用户', error='该服务商不允许此用户')
    client = FakeClient([failing, failing])

    daemon = Daemon(
        cfg,
        client,
        notifier=RecordingNotifier(),
        listener=lambda event, payload: events.append(event),
    )

    daemon.login_once()
    daemon.login_once()

    assert events.count('service_rejected') == 1


def test_daemon_ignores_unrelated_error(config_factory):
    cfg = config_factory()
    events = []
    client = FakeClient([AuthResult(False, 'fail', '密码错误', error='密码错误')])
    daemon = Daemon(cfg, client, notifier=RecordingNotifier(), listener=lambda event, payload: events.append(event))

    daemon.login_once()

    assert 'service_rejected' not in events


def test_daemon_update_service_updates_cfg(config_factory):
    cfg = config_factory()
    daemon = Daemon(cfg, FakeClient(), notifier=RecordingNotifier())

    daemon.update_service('中国移动')

    assert cfg['auto']['service'] == '中国移动'


# --------------------------------------------------------------------------- #
# 控制器：保存账号与服务商切换
# --------------------------------------------------------------------------- #
def test_controller_save_profile_writes_file(tmp_path):
    cfg = minimal_cfg(tmp_path)
    Path(cfg['_config_path']).write_text('main:\n  version: 3\n', encoding='utf-8')
    controller = AppController(cfg)

    message = controller.save_profile('2023000001', 'secret', '中国电信', ['中国电信', '中国移动'])

    assert '已保存' in message
    data = yaml.safe_load(Path(cfg['_config_path']).read_text(encoding='utf-8'))
    assert data['auto']['enabled'] is True
    assert data['auto']['user_id'] == '2023000001'
    assert data['auto']['password'].startswith('dpapi:')
    assert winutil.dpapi_unprotect(data['auto']['password']) == 'secret'
    assert data['auto']['service'] == '中国电信'
    assert data['auto']['service_options'] == ['中国电信', '中国移动']
    assert controller.needs_profile is False


def test_controller_apply_service_restarts_watch(tmp_path):
    cfg = minimal_cfg(tmp_path, enabled=True, user_id='2023000001', password='secret')
    Path(cfg['_config_path']).write_text('main:\n  version: 3\nauto:\n  service: ""\n', encoding='utf-8')
    controller = AppController(cfg)

    called = {}

    class FakeThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    controller._watch_thread = FakeThread()
    controller.stop_watch = lambda: called.setdefault('stop', True) or True
    controller.start_watch = lambda: called.setdefault('start', True) or True

    message = controller.apply_service('中国移动')

    assert '已改为' in message
    assert called.get('stop') and called.get('start')
    assert cfg['auto']['service'] == '中国移动'
    data = yaml.safe_load(Path(cfg['_config_path']).read_text(encoding='utf-8'))
    assert data['auto']['service'] == '中国移动'


def test_controller_service_options_fallback(tmp_path):
    cfg = minimal_cfg(tmp_path)
    controller = AppController(cfg)

    options = controller.service_options()

    assert options  # 没有缓存时使用内置兜底
    assert '中国电信' in options


def test_controller_service_options_prefers_cache(tmp_path):
    cfg = minimal_cfg(tmp_path)
    cfg['auto']['service_options'] = ['校园网A', '校园网B']

    assert AppController(cfg).service_options() == ['校园网A', '校园网B']


# --------------------------------------------------------------------------- #
# 界面冒烟（无桌面会话时跳过）
# --------------------------------------------------------------------------- #
tk = pytest.importorskip('tkinter')


def _can_create_window() -> bool:
    # tkinter 在连续创建/销毁时偶尔会失败，重试几次让判定更稳定
    for _attempt in range(3):
        try:
            root = tk.Tk()
            root.withdraw()
            root.destroy()
            return True
        except Exception:
            continue
    return False


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_setup_wizard_smoke():
    from campus_network_launcher.gui.dialogs import SetupWizard

    root = tk.Tk()
    root.withdraw()
    saved = {}

    def on_save(user_id, password, service, options):
        saved.update({'user_id': user_id, 'password': password, 'service': service, 'options': options})

    try:
        wizard = SetupWizard(
            root,
            services=['中国电信', '中国移动'],
            on_save=on_save,
            on_fetch_services=None,
        )
        wizard.user_var.set('2023000001')
        wizard.password_var.set('secret')
        wizard.service_var.set('中国移动')
        wizard._on_confirm()
    finally:
        root.destroy()

    assert saved['user_id'] == '2023000001'
    assert saved['password'] == 'secret'
    assert saved['service'] == '中国移动'
    assert saved['options'] == ['中国电信', '中国移动']


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_setup_wizard_requires_fields():
    from tkinter import messagebox

    from campus_network_launcher.gui.dialogs import SetupWizard

    root = tk.Tk()
    root.withdraw()
    warnings = []

    original = messagebox.showwarning
    messagebox.showwarning = lambda title, message, **kwargs: warnings.append((title, message))
    try:
        wizard = SetupWizard(root, services=['中国电信'], on_save=lambda *args: None)
        wizard.user_var.set('')
        wizard.password_var.set('secret')
        wizard._on_confirm()
    finally:
        messagebox.showwarning = original
        root.destroy()

    assert warnings  # 提示了"请填写学号"


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_service_picker_smoke():
    from campus_network_launcher.gui.dialogs import ServicePicker

    root = tk.Tk()
    root.withdraw()
    chosen = {}

    try:
        picker = ServicePicker(
            root,
            current='中国电信',
            services=['中国电信', '中国移动'],
            reason='该服务商不允许此用户',
            on_confirm=lambda service: chosen.setdefault('service', service),
        )
        picker.service_var.set('中国移动')
        picker._on_confirm()
    finally:
        root.destroy()

    assert chosen['service'] == '中国移动'


# --------------------------------------------------------------------------- #
# v1.12：主界面切换服务商 / 认证成功但网不通的提示
# --------------------------------------------------------------------------- #
def test_daemon_emits_service_suspected_when_still_offline(config_factory):
    """登录一直成功但网络始终不通时，应提示可能是服务商不对（只提示一次）。"""

    cfg = config_factory(extra='daemon:\n  check_interval: 5\n')
    events = []
    clock = FakeClock()
    daemon = Daemon(
        cfg,
        FakeClient([AuthResult(True, 'success', '')] * 6),
        notifier=RecordingNotifier(),
        sleep=clock.sleep,
        now=clock.now,
        online_probe=lambda: offline_probe(),
        reach_probe=lambda: True,
        listener=lambda event, payload: events.append(event),
    )

    daemon.run(max_iterations=4)

    assert events.count('service_suspected') == 1


def test_daemon_no_suspicion_when_network_ok(config_factory):
    cfg = config_factory()
    events = []
    clock = FakeClock()
    daemon = Daemon(
        cfg,
        FakeClient(),
        notifier=RecordingNotifier(),
        sleep=clock.sleep,
        now=clock.now,
        online_probe=lambda: online_probe(),
        reach_probe=lambda: True,
        listener=lambda event, payload: events.append(event),
    )

    daemon.run(max_iterations=3)

    assert 'service_suspected' not in events


def test_update_service_resets_suspicion(config_factory):
    cfg = config_factory()
    daemon = Daemon(cfg, FakeClient(), notifier=RecordingNotifier())

    daemon._service_suspected.add('中国电信')
    daemon.update_service('中国移动')

    assert daemon._service_suspected == set()
    assert cfg['auto']['service'] == '中国移动'


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_main_window_can_apply_service(tmp_path, monkeypatch):
    """主界面下拉框 + 应用按钮：把新服务商交给控制器写入配置。"""

    from campus_network_launcher.gui.app import AppWindow

    cfg = minimal_cfg(tmp_path, enabled=False, user_id='2023000001', password='secret', service='中国电信')
    Path(cfg['_config_path']).write_text('main:\n  version: 3\nauto:\n  service: 中国电信\n', encoding='utf-8')
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)
    applied = {}
    try:
        def fake_apply(service):
            applied['service'] = service
            return '服务商已改为「%s」，正在重新连接…' % service

        monkeypatch.setattr(controller, 'apply_service', fake_apply)
        window.service_var.set('中国移动')
        window._on_apply_service()
        log_text = window.log_text.get('1.0', 'end')
    finally:
        window.quit()

    assert applied.get('service') == '中国移动'
    assert '已改为' in log_text


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_main_window_service_combo_has_options(tmp_path):
    """主界面应带一个可编辑的服务商下拉框。"""

    from campus_network_launcher.gui.app import AppWindow

    cfg = minimal_cfg(tmp_path)
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)
    try:
        values = list(window.service_combo['values'])
        assert values  # 至少有内置兜底
        assert '中国电信' in values
    finally:
        window.quit()


# --------------------------------------------------------------------------- #
# v2.0.0：在线刷新服务商、主界面刷新按钮、署名
# --------------------------------------------------------------------------- #
def test_portal_url_from_config_variants():
    from campus_network_launcher.portal import _portal_url_from_config

    assert _portal_url_from_config(
        {'headers': {'Referer': 'http://a/eportal/index.jsp?x=1'}}
    ) == 'http://a/eportal/index.jsp?x=1'
    assert _portal_url_from_config(
        {'url': {'server': 'http://a/'}, 'login_data': {'queryString': 'wlanuserip%3D1'}}
    ) == 'http://a/eportal/index.jsp?wlanuserip%3D1'
    assert _portal_url_from_config({}) == ''


def test_fetch_service_options_when_online_uses_config(portal):
    """已在线时用配置里的认证页地址也能刷新服务商列表。"""

    portal.state.online = True
    portal_url = portal.base_url + '/eportal/index.jsp?wlanuserip=1.2.3.4&wlanacname=mock'
    cfg = {
        'network': {'probes': [portal.base_url + '/generate_204'], 'timeout': 3},
        'headers': {'Referer': portal_url},
        'login_data': {'queryString': 'wlanuserip%3D1.2.3.4'},
    }

    services, error = fetch_service_options(cfg)

    assert error == ''
    assert any('中国电信' in item for item in services)


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_main_window_refresh_services(tmp_path, monkeypatch):
    from campus_network_launcher.gui.app import AppWindow

    cfg = minimal_cfg(tmp_path)
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)
    called = {}
    try:
        monkeypatch.setattr(
            controller,
            'fetch_services_async',
            lambda done: called.setdefault('done', done) or True,
        )
        window._on_refresh_services()
        assert 'done' in called

        window._on_services_refreshed(['教师免费', '中国电信'], '')
        values = list(window.service_combo['values'])
        log_ok = window.log_text.get('1.0', 'end')

        window._on_services_refreshed([], '网络不可达')
        values_after = list(window.service_combo['values'])
        log_fail = window.log_text.get('1.0', 'end')
    finally:
        window.quit()

    assert values == ['教师免费', '中国电信']
    assert '已刷新服务商列表' in log_ok
    assert values_after == ['教师免费', '中国电信']  # 失败时不清空旧列表
    assert '刷新服务商列表失败' in log_fail


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_window_title_and_credit(tmp_path):
    from campus_network_launcher.gui.app import AppWindow

    cfg = minimal_cfg(tmp_path)
    window = AppWindow(AppController(cfg), start_watching=False, start_hidden=True)
    try:
        title = window.root.title()
        credit = window.credit_label.cget('text')
    finally:
        window.quit()

    assert title.startswith('Campus Network Launcher v')
    assert 'by:Xiaowu' in title
    assert credit == 'by:Xiaowu'


# --------------------------------------------------------------------------- #
# v2.0.1：密码加密 / 在线抓参 / 一键执行 / 弹窗开关
# --------------------------------------------------------------------------- #
def test_password_saved_encrypted_and_loaded_plain(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\n', encoding='utf-8')

    config.update_auto_fields(
        str(path), {'enabled': True, 'user_id': '2023000001', 'password': 'my-secret'}
    )

    text = path.read_text(encoding='utf-8')
    assert 'my-secret' not in text  # 明文不落盘
    assert 'dpapi:' in text
    data = yaml.safe_load(text)
    assert winutil.dpapi_unprotect(data['auto']['password']) == 'my-secret'

    loaded = config.load_config(str(path))
    assert loaded['auto']['password'] == 'my-secret'  # 内存里是明文，登录照常可用


def test_migrate_plain_password(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text(
        'main:\n  version: 3\nauto:\n  enabled: true\n  user_id: "2023000001"\n  password: "plain-pwd"\n',
        encoding='utf-8',
    )

    assert config.migrate_plain_password(str(path)) is True
    text = path.read_text(encoding='utf-8')
    assert 'plain-pwd' not in text
    assert 'dpapi:' in text
    # 已经是密文时不再迁移
    assert config.migrate_plain_password(str(path)) is False


def test_decrypt_failure_clears_password(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text(
        'main:\n  version: 3\nauto:\n  enabled: true\n  user_id: "2023000001"\n  password: "dpapi:not-a-valid-token"\n',
        encoding='utf-8',
    )

    cfg = config.load_config(str(path), allow_incomplete=True)

    assert cfg['auto']['password'] == ''
    assert cfg.get('_password_lost') is True


def test_setup_config_online_uses_portal_url(portal):
    from campus_network_launcher.portal import PortalDiscovery

    portal.state.online = True
    portal_url = portal.base_url + '/eportal/index.jsp?wlanuserip=1.2.3.4&wlanacname=mock'
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    discovered = discovery.setup_config('2023000001', 'secret', '中国电信', portal_url=portal_url)

    assert discovered['details']['source'] == 'online'
    assert discovered['login_data']['queryString']


def test_setup_config_online_without_portal_url_raises(portal):
    from campus_network_launcher.portal import AutoLoginError, PortalDiscovery

    portal.state.online = True
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    with pytest.raises(AutoLoginError):
        discovery.setup_config('2023000001', 'secret', '中国电信')


def test_suppress_portal_popup_toggle(tmp_path, monkeypatch):
    cfg = minimal_cfg(tmp_path)
    controller = AppController(cfg)
    calls = {}

    def fake_set(enabled):
        calls['enabled'] = enabled
        return True, '已执行'

    monkeypatch.setattr(winutil, 'set_active_probing', fake_set)
    monkeypatch.setattr(winutil, 'active_probing_enabled', lambda: False)

    ok, message = controller.set_suppress_portal_popup(True)

    assert ok and '已执行' in message
    assert calls['enabled'] is False  # 屏蔽 = 关闭主动探测
    assert controller.suppress_portal_popup_enabled() is True


def test_bootstrap_first_run_offline(tmp_path, monkeypatch):
    from campus_network_launcher.portal import PortalDiscovery

    cfg = minimal_cfg(tmp_path, enabled=True, user_id='2023000001', password='secret')
    Path(cfg['_config_path']).write_text('main:\n  version: 3\n', encoding='utf-8')
    controller = AppController(cfg)
    events = []
    monkeypatch.setattr(controller, '_post', lambda kind, message='', **data: events.append((kind, message)))
    monkeypatch.setattr(controller.daemon, 'status', lambda: {'online': False})
    monkeypatch.setattr(controller.daemon, 'login_once', lambda: 0)
    monkeypatch.setattr(controller, 'reload_config', lambda: 'ok')

    fake_discovered = {
        'url': {'server': 'http://x', 'login': '/l', 'logout': '/o'},
        'cookie': '',
        'login_data': {'userId': '2023000001', 'queryString': 'q'},
        'headers': {},
        'details': {'service_used': '中国电信', 'source': 'probe'},
    }
    monkeypatch.setattr(PortalDiscovery, 'setup_config', lambda self, *args, **kwargs: fake_discovered)

    assert controller.bootstrap_first_run() is True
    deadline = time.time() + 5
    while controller.busy and time.time() < deadline:
        time.sleep(0.05)

    kinds = [kind for kind, _message in events]
    assert 'bootstrap_progress' in kinds
    assert 'bootstrap_ok' in kinds


@pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')
def test_setup_wizard_mentions_one_click():
    from campus_network_launcher.gui.dialogs import SetupWizard

    root = tk.Tk()
    root.withdraw()
    try:
        wizard = SetupWizard(root, services=['中国电信'], on_save=lambda *args: None)
        hint = wizard.hint_var.get()
    finally:
        root.destroy()

    assert '一键执行' in hint
