#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免抓包（auto）功能测试：RSA 加密、参数发现、service 选择与完整登录流程。"""

from __future__ import annotations

import urllib.parse

import pytest

from ruijie_eporta.portal import (
    AutoLoginError,
    PortalDiscovery,
    build_encrypted_password,
    choose_service,
    normalize_service,
    rsa_encrypt_raw,
)
from tests.mock_portal import MockPortalServer


@pytest.fixture
def portal():
    with MockPortalServer() as server:
        yield server


# --------------------------------------------------------------------------- #
# RSA 加密（复刻认证页 security.js）
# --------------------------------------------------------------------------- #
def test_rsa_output_length_matches_modulus():
    modulus = 'c' * 256
    output = rsa_encrypt_raw('hello', modulus, '10001')

    assert len(output) == 256
    assert ' ' not in output
    assert all(ch in '0123456789abcdef' for ch in output)


def test_rsa_short_modulus_gives_shorter_cipher():
    output = rsa_encrypt_raw('hello', 'c' * 8, '10001')
    # 短模数下分组变小：每段长度等于模数的十六进制长度
    assert all(len(part) == 8 for part in output.split(' '))


def test_rsa_splits_long_plaintext_into_blocks():
    # modulus 8 hex → digits=2 → chunkSize=2 → 4 个字符会拆成 2 个分组
    output = rsa_encrypt_raw('abcd', 'c' * 8, '10001')
    parts = output.split(' ')

    assert len(parts) == 2
    assert all(len(part) == 8 for part in parts)


def test_rsa_requires_modulus():
    with pytest.raises(AutoLoginError):
        rsa_encrypt_raw('hello', '', '10001')


def test_rsa_is_deterministic_and_order_sensitive():
    modulus = 'c' * 256
    first = rsa_encrypt_raw('abcdef', modulus, '10001')
    second = rsa_encrypt_raw('abcdef', modulus, '10001')
    reversed_input = rsa_encrypt_raw('fedcba', modulus, '10001')

    assert first == second
    assert first != reversed_input


def test_build_encrypted_password_uses_mac_suffix():
    modulus = 'c' * 256
    with_mac = build_encrypted_password('secret123', 'aabbcc', modulus, '10001')
    other_mac = build_encrypted_password('secret123', 'ddeeff', modulus, '10001')
    fallback = build_encrypted_password('secret123', '', modulus, '10001')
    default_mac = build_encrypted_password('secret123', '111111111', modulus, '10001')

    assert with_mac != other_mac
    assert fallback == default_mac


# --------------------------------------------------------------------------- #
# service 选择
# --------------------------------------------------------------------------- #
SERVICES = {
    'default': {'serviceName': 'default', 'serviceShowName': '教师免费', 'serviceDefault': 'true'},
    '中国电信': {'serviceName': '中国电信', 'serviceShowName': '中国电信（宿舍）', 'serviceDefault': 'false'},
}


def test_choose_service_matches_chinese_name():
    assert choose_service({'service': SERVICES}, '中国电信') == '中国电信'


def test_choose_service_accepts_url_encoded_name():
    encoded = urllib.parse.quote('中国电信')
    assert choose_service({'service': SERVICES}, encoded) == '中国电信'


def test_choose_service_matches_show_name():
    assert choose_service({'service': SERVICES}, '中国电信（宿舍）') == '中国电信'


def test_choose_service_falls_back_to_server_default():
    assert choose_service({'service': SERVICES}, '') == 'default'


def test_choose_service_keeps_unknown_value():
    assert choose_service({'service': SERVICES}, '未知运营商') == '未知运营商'


def test_choose_service_falls_back_to_html_select():
    html = '<select id="service"><option value="校园网">校园网</option></select>'
    assert choose_service({}, '', html) == '校园网'


# --------------------------------------------------------------------------- #
# 探测与参数解析
# --------------------------------------------------------------------------- #
def test_detect_location_returns_portal_url(portal):
    portal.state.online = False
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    location = discovery.detect_location()

    assert location is not None
    assert '/eportal/index.jsp?' in location
    assert 'wlanuserip=' in location
    assert 'mac=' in location


def test_detect_location_returns_none_when_online(portal):
    portal.state.online = True
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    assert discovery.detect_location() is None


def test_detect_location_uses_next_probe(portal):
    portal.state.online = False
    probes = ['http://127.0.0.1:1/generate_204', portal.base_url + '/generate_204']
    discovery = PortalDiscovery(probes=probes, timeout=2)

    location = discovery.detect_location()

    assert location and '/eportal/index.jsp?' in location


def test_build_info_parses_and_encodes_query():
    discovery = PortalDiscovery()
    url = 'http://10.0.0.1/eportal/index.jsp?wlanuserip=abc&ssid=&mac=def&t=wireless-v2'
    info = discovery.build_info(url)

    assert info.server == 'http://10.0.0.1'
    assert info.query_string == 'wlanuserip=abc&ssid=&mac=def&t=wireless-v2'
    # 单次 URL 编码：= → %3D，& → %26（与认证页 JS 的 pageInfo 参数一致）
    assert info.encoded_query == 'wlanuserip%3Dabc%26ssid%3D%26mac%3Ddef%26t%3Dwireless-v2'
    assert info.mac == 'def'
    assert info.login_url == 'http://10.0.0.1/eportal/InterFace.do?method=login'


def test_build_info_rejects_invalid_url():
    discovery = PortalDiscovery()
    with pytest.raises(AutoLoginError):
        discovery.build_info('not-a-url')


def test_page_info_returns_public_key(portal):
    portal.state.online = False
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)
    info, _html = discovery.discover()

    page = discovery.page_info(info)

    assert page['passwordEncrypt'] == 'true'
    assert page['publicKeyModulus'] == portal.state.modulus
    assert page['publicKeyExponent'] == '10001'
    assert '中国电信' in page['service']


# --------------------------------------------------------------------------- #
# 完整流程
# --------------------------------------------------------------------------- #
def test_full_auto_login_success(portal):
    portal.state.online = False
    portal.state.expect_password = build_encrypted_password(
        portal.state.password, portal.state.mac, portal.state.modulus, portal.state.exponent
    )
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.login(portal.state.user_id, portal.state.password, '中国电信')

    assert result.ok is True
    assert portal.state.login_calls == 1
    form = portal.state.last_login_form
    assert form['userId'] == portal.state.user_id
    assert form['service'] == urllib.parse.quote('中国电信')
    assert form['passwordEncrypt'] == 'true'
    assert form['password'] == portal.state.expect_password
    # queryString 必须是"单次 URL 编码"形式
    assert form['queryString'] == urllib.parse.quote(portal.state.portal_query(), safe='')
    assert 'wlanuserip%3D' in form['queryString']
    # pageInfo 也应当带上同样编码的 queryString
    assert portal.state.last_page_info_form['queryString'] == form['queryString']


def test_full_auto_login_wrong_password(portal):
    portal.state.online = False
    portal.state.expect_password = 'f' * 256
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.login(portal.state.user_id, 'wrong-password', '中国电信')

    assert result.ok is False
    assert result.result == 'fail'


def test_login_skips_when_already_online(portal):
    portal.state.online = True
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.login(portal.state.user_id, portal.state.password)

    assert result.ok is True
    assert portal.state.login_calls == 0
    assert '已在线' in result.message


def test_login_without_encryption(portal):
    portal.state.online = False
    portal.state.password_encrypt = False
    portal.state.expect_password = portal.state.password
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.login(portal.state.user_id, portal.state.password, '中国电信')

    assert result.ok is True
    assert portal.state.last_login_form['password'] == portal.state.password
    assert portal.state.last_login_form['passwordEncrypt'] == 'false'


def test_login_requires_credentials(portal):
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    with pytest.raises(AutoLoginError):
        discovery.login('', 'secret123')


def test_login_survives_non_json_response(portal):
    portal.state.online = False
    portal.state.modulus = ''  # 触发异常分支：pageInfo 没给模数

    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)
    result = discovery.login(portal.state.user_id, portal.state.password, '中国电信')

    # 没有模数时应退回明文密码而不是崩溃
    assert result.ok is True
    assert portal.state.last_login_form['passwordEncrypt'] == 'false'

# --------------------------------------------------------------------------- #
# service 编码 与 自动配置（首次运行写回 config.yml）
# --------------------------------------------------------------------------- #
def test_normalize_service_is_single_encoding_and_idempotent():
    encoded = urllib.parse.quote('中国电信')

    assert normalize_service('中国电信') == encoded
    assert normalize_service(encoded) == encoded
    assert normalize_service('') == ''


def test_login_sends_encoded_service(portal):
    portal.state.online = False
    portal.state.expect_password = build_encrypted_password(
        portal.state.password, portal.state.mac, portal.state.modulus, portal.state.exponent
    )
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    discovery.login(portal.state.user_id, portal.state.password, '中国电信')

    assert portal.state.last_login_form['service'] == urllib.parse.quote('中国电信')


def test_setup_config_returns_writable_values(portal):
    portal.state.online = False
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.setup_config(portal.state.user_id, portal.state.password, '中国电信')

    assert result['url']['server'] == portal.base_url
    assert result['url']['login'].endswith('method=login')
    assert result['login_data']['userId'] == portal.state.user_id
    assert result['login_data']['passwordEncrypt'] == 'true'
    assert len(result['login_data']['password']) == len(portal.state.modulus)
    assert result['login_data']['service'] == urllib.parse.quote('中国电信')
    assert result['login_data']['queryString'] == urllib.parse.quote(portal.state.portal_query(), safe='')
    assert result['headers']['Referer'].startswith(portal.base_url + '/eportal/index.jsp?')
    assert 'JSESSIONID' in result['cookie']
    assert result['details']['password_encrypted'] is True
    assert result['details']['mac'] == portal.state.mac
    # 自动配置不应触发登录请求
    assert portal.state.login_calls == 0


def test_setup_config_requires_credentials(portal):
    portal.state.online = False
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    with pytest.raises(AutoLoginError):
        discovery.setup_config('', '')


def test_setup_config_refuses_when_online(portal):
    portal.state.online = True
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    with pytest.raises(AutoLoginError):
        discovery.setup_config(portal.state.user_id, portal.state.password)


def test_setup_config_without_encryption(portal):
    portal.state.online = False
    portal.state.password_encrypt = False
    portal.state.modulus = ''
    discovery = PortalDiscovery(probes=[portal.base_url + '/generate_204'], timeout=3)

    result = discovery.setup_config(portal.state.user_id, portal.state.password, '中国电信')

    assert result['login_data']['passwordEncrypt'] == 'false'
    assert result['login_data']['password'] == portal.state.password
