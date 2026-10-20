#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动配置测试：首次运行自动获取参数并写回 config.yml。"""

from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest
import yaml

from campus_network_launcher import config
from campus_network_launcher.gui.controller import AppController
from campus_network_launcher.portal import PortalDiscovery
from tests.mock_portal import MockPortalServer


@pytest.fixture
def portal():
    with MockPortalServer() as server:
        yield server


def minimal_cfg(**auto_overrides) -> dict:
    """构造一份最小可用配置字典（不依赖真实文件）。"""

    cfg = {
        'main': {'version': 3},
        'funtion': {'check_school_network': True, 'disconnect_network': True},
        'url': {'server': 'http://127.0.0.1:9', 'login': '/eportal/InterFace.do?method=login'},
        'cookie': '',
        'login_data': {'userId': '2023000001', 'queryString': ''},
        'logout_data': {},
        'headers': {},
        '_config_path': '',
    }
    for key, value in config.DEFAULT_OPTIONS.items():
        cfg.setdefault(key, copy.deepcopy(value))
    cfg['auto'] = {
        'enabled': True,
        'user_id': '2023000001',
        'password': 'secret123',
        'service': '中国电信',
    }
    cfg['auto'].update(auto_overrides)
    return cfg


def discover(portal: MockPortalServer, user_id: str = '', password: str = '', service: str = '中国电信'):
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)
    return discovery.setup_config(
        user_id or portal.state.user_id,
        password or portal.state.password,
        service,
    )


def wait_worker(controller: AppController, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not controller.busy:
            return
        time.sleep(0.02)
    raise AssertionError('后台任务超时')


def drain(controller: AppController):
    kinds = []
    while True:
        event = controller.poll_event()
        if event is None:
            break
        kinds.append(event.kind)
    return kinds


# --------------------------------------------------------------------------- #
# 由发现结果构造配置
# --------------------------------------------------------------------------- #
def test_build_config_from_discovery_has_all_sections(portal):
    portal.state.online = False
    discovered = discover(portal)

    cfg = config.build_config_from_discovery(discovered, auto_cfg={'enabled': True, 'user_id': 'x', 'password': 'y', 'service': '中国电信'})

    assert cfg['main']['version'] == 3
    assert cfg['url']['server'] == portal.base_url
    assert cfg['login_data']['userId'] == portal.state.user_id
    assert cfg['login_data']['queryString']
    assert cfg['auto']['enabled'] is True
    # 默认段（守护/探测/通知/自启）应当一并带上
    assert cfg['daemon']['check_interval'] == 30
    assert cfg['network']['probes']
    assert cfg['notify']['toast'] is True
    assert cfg['autostart']['method'] == 'startup'


def test_discovered_header_mentions_server(portal):
    portal.state.online = False
    discovered = discover(portal)

    header = config.discovered_header(discovered)

    assert '自动探测生成' in header
    assert portal.base_url in header
    assert '中国电信' in header


# --------------------------------------------------------------------------- #
# 写入配置文件
# --------------------------------------------------------------------------- #
def test_save_config_writes_loadable_file(portal, tmp_path):
    portal.state.online = False
    discovered = discover(portal)
    cfg = config.build_config_from_discovery(discovered, auto_cfg=config.DEFAULT_OPTIONS['auto'])

    target = str(tmp_path / 'config.yml')
    path, backup = config.save_config(target, cfg, header=config.discovered_header(discovered))

    assert path == target
    assert backup == ''  # 原本没有文件，所以没有备份

    text = Path(target).read_text(encoding='utf-8')
    assert '自动探测生成' in text

    loaded = config.load_config(target)  # 必须能被自己的解析器接受
    assert loaded['main']['version'] == 3
    assert loaded['login_data']['userId'] == portal.state.user_id
    assert loaded['login_data']['queryString'] == discovered['login_data']['queryString']
    assert loaded['url']['server'] == portal.base_url
    assert 'JSESSIONID' in loaded['cookie']


def test_save_config_backs_up_existing(portal, tmp_path):
    portal.state.online = False
    discovered = discover(portal)
    cfg = config.build_config_from_discovery(discovered, auto_cfg=config.DEFAULT_OPTIONS['auto'])

    target = tmp_path / 'config.yml'
    target.write_text('main:\n  version: 3\n# 旧内容\n', encoding='utf-8')

    _path, backup = config.save_config(str(target), cfg)

    assert backup and Path(backup).is_file()
    assert '旧内容' in Path(backup).read_text(encoding='utf-8')
    assert '旧内容' not in target.read_text(encoding='utf-8')


def test_saved_config_can_be_reloaded_by_program(portal, tmp_path):
    """写出的配置应当能直接驱动抓包模式登录（不再依赖 auto 段）。"""

    portal.state.online = False
    discovered = discover(portal)
    auto_cfg = dict(config.DEFAULT_OPTIONS['auto'])  # enabled=False
    cfg = config.build_config_from_discovery(discovered, auto_cfg=auto_cfg)
    target = str(tmp_path / 'config.yml')
    config.save_config(target, cfg, header=config.discovered_header(discovered))

    loaded = config.load_config(target)
    from campus_network_launcher.eportal import EportalClient

    result = EportalClient(loaded).login()

    assert result.ok is True
    assert portal.state.login_calls == 1
    assert portal.state.last_login_form['queryString'] == discovered['login_data']['queryString']


# --------------------------------------------------------------------------- #
# 界面控制器的自动配置
# --------------------------------------------------------------------------- #
def test_needs_auto_setup_when_enabled_without_query():
    controller = AppController(minimal_cfg())

    assert controller.needs_auto_setup() is True


def test_needs_auto_setup_false_without_password():
    assert AppController(minimal_cfg(password='')).needs_auto_setup() is False


def test_needs_auto_setup_false_when_disabled():
    assert AppController(minimal_cfg(enabled=False)).needs_auto_setup() is False


def test_needs_auto_setup_false_when_already_has_query():
    cfg = minimal_cfg()
    cfg['login_data']['queryString'] = 'wlanuserip%3Dabc'

    assert AppController(cfg).needs_auto_setup() is False


def test_controller_auto_setup_writes_config(portal, tmp_path):
    portal.state.online = False
    target = tmp_path / 'config.yml'
    target.write_text('main:\n  version: 3\n', encoding='utf-8')

    cfg = minimal_cfg()
    cfg['_config_path'] = str(target)
    cfg['network']['probes'] = [portal.base_url + '/generate_204']
    cfg['network']['timeout'] = 3
    controller = AppController(cfg)

    assert controller.auto_setup() is True
    wait_worker(controller)

    loaded = config.load_config(str(target))
    assert loaded['login_data']['userId'] == '2023000001'
    assert loaded['login_data']['passwordEncrypt'] == 'true'
    assert len(loaded['login_data']['password']) == len(portal.state.modulus)
    assert loaded['login_data']['service'] == discovered_service()
    assert 'auto_setup_ok' in drain(controller)
    # 原文件已被备份
    backups = list(tmp_path.glob('config.yml.bak-*'))
    assert backups, '应当生成备份文件'


def discovered_service() -> str:
    import urllib.parse

    return urllib.parse.quote('中国电信')


def test_controller_auto_setup_reports_failure(portal, tmp_path):
    portal.state.online = True  # 已在线 → 拿不到认证页参数
    target = tmp_path / 'config.yml'
    target.write_text('main:\n  version: 3\n', encoding='utf-8')

    cfg = minimal_cfg()
    cfg['_config_path'] = str(target)
    cfg['network']['probes'] = [portal.base_url + '/generate_204']
    controller = AppController(cfg)

    controller.auto_setup()
    wait_worker(controller)

    kinds = drain(controller)
    assert 'error' in kinds
    assert target.read_text(encoding='utf-8').strip().endswith('version: 3')
