#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免抓包流程离线验证（只读，不会发送登录请求）。

用已有配置里的认证页地址走一遍「打开认证页 → pageInfo → 加密密码」，
并把自动生成的 queryString / 密文长度与配置中抓包得到的值对比。

用法：
    .venv\\Scripts\\python.exe tools\\verify_auto.py "D:\\path\\config.yml"
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from campus_network_launcher.portal import PortalDiscovery, build_encrypted_password  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print('用法：verify_auto.py <config.yml 路径>')
        return 1

    with open(sys.argv[1], 'r', encoding='utf-8') as fp:
        cfg = yaml.safe_load(fp)

    location = str((cfg.get('headers') or {}).get('Referer') or '')
    if not location:
        print('配置里没有 headers.Referer，无法进行本项验证')
        return 1
    expected_query = str((cfg.get('login_data') or {}).get('queryString') or '')
    expected_password = str((cfg.get('login_data') or {}).get('password') or '')

    discovery = PortalDiscovery(timeout=8)
    info = discovery.build_info(location)

    print('认证服务器        :', info.server)
    print('mac 参数          :', info.mac)
    same = info.encoded_query == expected_query
    print('自动生成的 queryString 与抓包一致 :', same)
    if not same:
        print('  自动：', info.encoded_query[:100])
        print('  抓包：', expected_query[:100])

    html = discovery.open_portal(info)
    print('认证页长度        :', len(html))
    services = discovery.parse_services(html)
    print('运营商选项        :', services)

    page = discovery.page_info(info)
    print('passwordEncrypt   :', page.get('passwordEncrypt'))
    print('publicKeyExponent :', page.get('publicKeyExponent'))
    modulus = str(page.get('publicKeyModulus') or '')
    print('publicKeyModulus  : %d 字符  %s...' % (len(modulus), modulus[:40]))

    if modulus:
        encrypted = build_encrypted_password('test1234', info.mac, modulus, str(page.get('publicKeyExponent') or '10001'))
        print('本地加密结果长度  :', len(encrypted), '（抓包密文长度：%d）' % len(expected_password))
        print('本地加密结果      :', encrypted[:64], '...')
        print('长度是否一致      :', len(encrypted) == len(expected_password))

    others = {k: v for k, v in page.items() if k != 'publicKeyModulus'}
    print('pageInfo 其它字段 :', json.dumps(others, ensure_ascii=False)[:500])
    return 0


if __name__ == '__main__':
    sys.exit(main())
