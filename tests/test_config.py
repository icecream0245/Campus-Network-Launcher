#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置模块测试：重点验证与上游 v3 配置的兼容性。"""

from __future__ import annotations

import os

import pytest

from campus_network_launcher import config
from tests.conftest import write_config_file


def test_upstream_v3_config_is_accepted(config_factory):
    cfg = config_factory()

    assert cfg['main']['version'] == 3
    assert cfg['url']['server'] == 'http://127.0.0.1:9'
    assert cfg['login_data']['userId'] == '2023000001'
    # 上游的拼写错误必须被兼容
    assert cfg['funtion']['check_school_network'] is True
    assert cfg['funtion']['disconnect_network'] is True
    # 表单字段统一转成字符串（布尔值转小写）
    assert cfg['login_data']['passwordEncrypt'] == 'true'
    assert cfg['login_data']['operatorPwd'] == ''
    assert cfg['logout_data'] == {}
    assert cfg['_config_path'].endswith('config.yml')


def test_new_sections_get_defaults(config_factory):
    cfg = config_factory()

    assert cfg['daemon']['check_interval'] == 30
    assert cfg['daemon']['max_retry_interval'] == 300
    assert cfg['network']['probes'][0].endswith('generate_204')
    assert cfg['notify']['toast'] is True
    assert cfg['notify']['quiet'] is False
    assert cfg['autostart']['method'] == 'startup'
    assert '--watch' in cfg['autostart']['args']


def test_new_sections_override_defaults(config_factory):
    cfg = config_factory(
        extra='''
daemon:
  check_interval: 7
  retry_interval: 2
  max_retry_interval: 1
  cooldown_after_failures: 3
  cooldown: 120
  keepalive_interval: 90
network:
  probes: 'http://example.com/generate_204'
  timeout: 5
notify:
  quiet: true
  dialog: true
autostart:
  method: registry
  args: --watch
'''
    )

    assert cfg['daemon']['check_interval'] == 7
    assert cfg['daemon']['retry_interval'] == 2
    # max_retry_interval 不允许小于 retry_interval
    assert cfg['daemon']['max_retry_interval'] == 2
    assert cfg['daemon']['keepalive_interval'] == 90
    assert cfg['network']['probes'] == ['http://example.com/generate_204']
    assert cfg['network']['timeout'] == 5
    assert cfg['notify']['quiet'] is True
    assert cfg['notify']['dialog'] is True
    assert cfg['autostart']['method'] == 'registry'
    assert cfg['autostart']['args'] == '--watch'


def test_string_booleans_are_parsed(config_factory):
    cfg = config_factory(check_school_network='false', disconnect_network='"false"')
    assert cfg['funtion']['check_school_network'] is False
    assert cfg['funtion']['disconnect_network'] is False


def test_trailing_slash_removed(config_factory):
    cfg = config_factory(server='http://10.0.0.1/')
    assert cfg['url']['server'] == 'http://10.0.0.1'


def test_missing_config_creates_template(tmp_path):
    path = os.path.join(str(tmp_path), 'nope', 'config.yml')
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert '已生成配置模板' in str(excinfo.value)
    assert os.path.isfile(path)

    with open(path, 'r', encoding='utf-8') as fp:
        text = fp.read()
    assert 'daemon:' in text and 'autostart:' in text

    # 模板里的 userId 仍是占位值，因此直接加载应当报错
    with pytest.raises(config.ConfigError) as excinfo2:
        config.load_config(path, create_if_missing=False)
    assert 'userId' in str(excinfo2.value)


def test_missing_config_without_creation(tmp_path):
    path = os.path.join(str(tmp_path), 'absent.yml')
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path, create_if_missing=False)
    assert '配置文件不存在' in str(excinfo.value)
    assert not os.path.exists(path)


@pytest.mark.parametrize('version', [1, 2, 4, 99])
def test_version_mismatch_rejected(tmp_path, version):
    path = os.path.join(str(tmp_path), 'config.yml')
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write('main:\n  version: %s\n' % version)
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_placeholder_user_id_rejected(config_factory):
    with pytest.raises(config.ConfigError) as excinfo:
        config_factory(user_id='00000000000')
    assert 'userId' in str(excinfo.value)


def test_empty_server_rejected(config_factory):
    with pytest.raises(config.ConfigError):
        config_factory(server='""')


def test_blank_login_url_rejected(tmp_path):
    path = write_config_file(str(tmp_path))
    with open(path, 'r', encoding='utf-8') as fp:
        text = fp.read()
    text = text.replace('/eportal/InterFace.do?method=login', "''")
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(text)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert 'url.login' in str(excinfo.value)


def test_invalid_yaml_rejected(tmp_path):
    path = os.path.join(str(tmp_path), 'config.yml')
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write('main: [unclosed\n')
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert 'YAML' in str(excinfo.value)


def test_root_not_mapping_rejected(tmp_path):
    path = os.path.join(str(tmp_path), 'config.yml')
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write('- a\n- b\n')
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert '映射' in str(excinfo.value)


def test_env_var_sets_default_path(monkeypatch, tmp_path):
    path = write_config_file(str(tmp_path), filename='custom.yml')
    monkeypatch.setenv(config.ENV_CONFIG_PATH, path)
    assert config.default_config_path() == os.path.abspath(path)
    cfg = config.load_config()
    assert cfg['login_data']['userId'] == '2023000001'


def test_program_dir_is_project_root():
    assert os.path.isfile(os.path.join(config.program_dir(), 'pyproject.toml'))


def test_probe_mapping_is_normalized(config_factory):
    cfg = config_factory(
        extra=(
            'network:\n'
            '  probes:\n'
            '    - url: http://a.example/generate_204\n'
            '    - url: http://b.example/connecttest.txt\n'
            '      contains: ok\n'
            '      expect_status: [200, 204]\n'
        )
    )

    probes = cfg['network']['probes']
    # 映射写法原样保留（缺少可选字段时不强行转换）
    assert probes[0] == {'url': 'http://a.example/generate_204'}
    assert probes[1]['url'] == 'http://b.example/connecttest.txt'
    assert probes[1]['contains'] == 'ok'
    assert probes[1]['expect_status'] == [200, 204]


def test_probe_list_ignores_invalid_entries(config_factory):
    cfg = config_factory(extra='network:\n  probes:\n    - ""\n    - url: ""\n')
    # 全部无效时回退到默认探测列表
    assert cfg['network']['probes'] == config.DEFAULT_OPTIONS['network']['probes']


def test_default_probe_has_content_check(config_factory):
    probes = config_factory()['network']['probes']
    assert isinstance(probes[0], str)
    assert isinstance(probes[1], dict)
    assert probes[1]['contains'] == 'Microsoft Connect Test'
