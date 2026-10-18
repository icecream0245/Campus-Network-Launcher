#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次运行向导 / 服务商选择 / 配置写回 的测试（全部离线，不连真实校园网）。"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
import yaml

from ruijie_eporta import config
from ruijie_eporta.daemon import Daemon
from ruijie_eporta.eportal import AuthResult
from ruijie_eporta.gui.controller import AppController
from ruijie_eporta.portal import fetch_service_options, is_service_error
from tests.conftest import FakeClient, RecordingNotifier
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
    assert data['auto'] == {
        'enabled': True,
        'user_id': '2023000001',
        'password': 'secret',
        'service': '中国电信',
        'service_options': ['中国电信', '中国移动'],
    }
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
    assert data['auto']['password'] == 'p'


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
    assert services[0] == 'default'  # 默认项排在最前
    assert '中国电信' in services
    assert '中国移动' in services


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
    assert data['auto']['password'] == 'secret'
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
    from ruijie_eporta.gui.dialogs import SetupWizard

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

    from ruijie_eporta.gui.dialogs import SetupWizard

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
    from ruijie_eporta.gui.dialogs import ServicePicker

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
