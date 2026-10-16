#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证「自动配置」：启动模拟认证门户，跑真实 CLI，检查写入的配置文件。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\e2e_auto_setup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # noqa: E402

from tests.mock_portal import MockPortalServer  # noqa: E402

FAILURES = []


def check(condition: bool, description: str) -> None:
    print('  [%s] %s' % ('PASS' if condition else 'FAIL', description))
    if not condition:
        FAILURES.append(description)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except Exception:
            pass

    with MockPortalServer() as server, tempfile.TemporaryDirectory(prefix='eporta-setup-') as workdir:
        server.state.online = False
        config_path = os.path.join(workdir, 'config.yml')

        # 模拟"用户刚拿到程序、还没抓包"的配置
        initial = {
            'main': {'version': 3},
            'funtion': {'check_school_network': True, 'disconnect_network': True},
            'url': {'server': 'http://127.0.0.1:9', 'login': '/eportal/InterFace.do?method=login'},
            'cookie': '',
            'login_data': {'userId': '00000000000'},
            'headers': {},
            'network': {'probes': [server.base_url + '/generate_204'], 'timeout': 3},
            'auto': {'enabled': True, 'user_id': '2023000001', 'password': 'secret123', 'service': '中国电信'},
        }
        with open(config_path, 'w', encoding='utf-8') as fp:
            yaml.safe_dump(initial, fp, allow_unicode=True, sort_keys=False)

        print('模拟认证门户：%s' % server.base_url)
        print('\n[1] 执行 --auto-setup')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        completed = subprocess.run(
            [sys.executable, '-m', 'ruijie_eporta', '--config', config_path, '--auto-setup', '--quiet', '--allow-multiple'],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
        )
        check(completed.returncode == 0, '退出码为 0（实际 %s）' % completed.returncode)
        if completed.returncode != 0:
            print(completed.stdout[-1500:])
            print(completed.stderr[-1500:])

        print('\n[2] 检查写入后的配置')
        with open(config_path, 'r', encoding='utf-8') as fp:
            text = fp.read()
        data = yaml.safe_load(text)

        check('自动探测生成' in text, '文件带有自动生成的说明头')
        check(data['url']['server'] == server.base_url, 'url.server 已写入（%s）' % data['url']['server'])
        check(data['login_data']['userId'] == '2023000001', 'login_data.userId 已写入')
        check(data['login_data']['passwordEncrypt'] == 'true', '密码标记为已加密')
        check(len(str(data['login_data']['password'])) == len(server.state.modulus), '密码密文长度与公钥一致（%d）' % len(str(data['login_data']['password'])))
        check(str(data['login_data']['queryString']).startswith('wlanuserip%3D'), 'queryString 为单次 URL 编码形式')
        expected_service = urllib.parse.quote('中国电信')
        check(data['login_data']['service'] == expected_service, 'service 以 URL 编码形式写入（%s）' % data['login_data']['service'])
        check('JSESSIONID' in str(data['cookie']), 'cookie 已写入（%s…）' % str(data['cookie'])[:40])
        check(str(data['headers']['Referer']).startswith(server.base_url + '/eportal/index.jsp?'), 'Referer 已写入')
        check(data['auto']['enabled'] is True, 'auto 段被保留')
        check(data['daemon']['check_interval'] == 30, '默认段（daemon）一并带上')

        backups = [name for name in os.listdir(workdir) if '.bak-' in name]
        check(bool(backups), '原配置已备份（%s）' % (backups[0] if backups else '-'))

        print('\n[3] 用写入后的配置执行抓包模式登录（auto 关闭）')
        data['auto']['enabled'] = False
        with open(config_path, 'w', encoding='utf-8') as fp:
            yaml.safe_dump(data, fp, allow_unicode=True, sort_keys=False)
        completed = subprocess.run(
            [sys.executable, '-m', 'ruijie_eporta', '--config', config_path, '--login', '--quiet', '--allow-multiple'],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
        )
        check(completed.returncode == 0, '登录退出码为 0（实际 %s）' % completed.returncode)
        check(server.state.login_calls == 1, '认证接口被调用 1 次（实际 %d）' % server.state.login_calls)
        check(server.state.last_login_form.get('queryString') == data['login_data']['queryString'], '登录时使用的 queryString 与写入的一致')

    print('\n' + '=' * 60)
    if FAILURES:
        print('端到端验证未通过，失败 %d 项：' % len(FAILURES))
        for item in FAILURES:
            print('  - %s' % item)
        return 1
    print('自动配置端到端验证全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
