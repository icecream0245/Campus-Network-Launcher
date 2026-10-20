#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自检：启动本地模拟 ePortal 服务器，跑真实的 CLI 流程。

覆盖场景：
  1. 未联网时 ``--login`` 能认证成功；
  2. ``--status`` 能正确报告在线状态；
  3. 模拟掉线后 ``--watch`` 能自动重连（核心新功能）；
  4. ``--logout`` 能下线；
  5. 日志文件正常落盘。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe tools\\e2e_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.mock_eportal import MockEportalServer  # noqa: E402

CONFIG_TEMPLATE = '''\
main:
  version: 3

funtion:
  check_school_network: true
  disconnect_network: true

url:
  server: {server}
  login: /ePortal/InterFace.do?method=login
  logout: /ePortal/InterFace.do?method=logout

cookie: 'EPORTAL_COOKIE_USERNAME=2023000001; JSESSIONID=E2E'

login_data:
  userId: '2023000001'
  password: '0123456789abcdef'
  service: '%E4%B8%AD%E5%9B%BD%E7%94%B5%E4%BF%A1'
  queryString: 'wlanuserip%3D1'
  passwordEncrypt: true

logout_data:

headers:
  Referer: {server}/eportal/index.jsp

daemon:
  check_interval: 1
  retry_interval: 1
  max_retry_interval: 2
  probe_timeout: 2
  login_timeout: 3

network:
  probes:
    - {server}/generate_204
  timeout: 2

notify:
  toast: false
  dialog: false
  quiet: true
'''

FAILURES = []
CLI_EXE: Optional[str] = None


def check(condition: bool, description: str) -> None:
    status = 'PASS' if condition else 'FAIL'
    print('  [%s] %s' % (status, description))
    if not condition:
        FAILURES.append(description)


def cli_command(args, config_path: str):
    """默认用源码运行；指定 --exe 时直接测试打包产物（需用 Console 版）。"""

    if CLI_EXE:
        return [CLI_EXE, '--config', config_path] + args
    return [sys.executable, '-m', 'campus_network_launcher', '--config', config_path] + args


def run_cli(args, config_path: str, timeout: float = 60.0):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    completed = subprocess.run(
        cli_command(args, config_path),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
    )
    return completed


def main() -> int:
    global CLI_EXE
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except Exception:
            pass

    import argparse

    parser = argparse.ArgumentParser(description='锐捷 ePortal 工具端到端自检')
    parser.add_argument('--exe', help='测试打包好的 Console 版 exe（默认用源码运行）')
    namespace = parser.parse_args()
    if namespace.exe:
        CLI_EXE = os.path.abspath(namespace.exe)
        if not os.path.isfile(CLI_EXE):
            print('找不到指定的 exe：%s' % CLI_EXE)
            return 1
        print('测试目标：%s' % CLI_EXE)

    with MockEportalServer() as server, tempfile.TemporaryDirectory(prefix='eporta-e2e-') as workdir:
        config_path = os.path.join(workdir, 'config.yml')
        with open(config_path, 'w', encoding='utf-8') as fp:
            fp.write(CONFIG_TEMPLATE.format(server=server.base_url))

        print('模拟认证服务器：%s' % server.base_url)

        print('\n[1] 未联网时执行 --login')
        result = run_cli(['--login', '--quiet', '--allow-multiple'], config_path)
        check(result.returncode == 0, '退出码为 0（实际 %s）' % result.returncode)
        check(server.state.login_calls == 1, '认证接口被调用 1 次（实际 %d）' % server.state.login_calls)
        check(server.state.online is True, '模拟服务器状态变为已联网')
        if result.returncode != 0:
            print(result.stdout[-2000:])
            print(result.stderr[-2000:])

        print('\n[2] 已联网时执行 --status')
        result = run_cli(['--status', '--quiet', '--allow-multiple'], config_path)
        payload = None
        try:
            payload = json.loads(result.stdout)
        except Exception:
            payload = None
        check(payload is not None, '--status 输出合法 JSON')
        if payload:
            check(payload.get('online') is True, '状态显示已联网')
            check(payload.get('school_network') is True, '状态显示校园网服务器可达')
            check(payload.get('user_id') == '2023000001', '状态显示账号正确')

        print('\n[3] 模拟掉线后执行 --watch（自动重连）')
        server.state.online = False
        before = server.state.login_calls
        started = time.time()
        result = run_cli(['--watch', '--iterations', '3', '--quiet', '--allow-multiple'], config_path, timeout=90)
        elapsed = time.time() - started
        check(result.returncode == 0, '守护模式正常退出（实际 %s）' % result.returncode)
        check(server.state.login_calls == before + 1, '掉线后自动发起了 1 次认证')
        check(server.state.online is True, '已自动恢复联网')
        check(elapsed < 30, '重连及时完成（耗时 %.1fs）' % elapsed)
        if result.returncode != 0:
            print(result.stdout[-2000:])
            print(result.stderr[-2000:])

        print('\n[4] 执行 --logout')
        result = run_cli(['--logout', '--quiet', '--allow-multiple'], config_path)
        check(result.returncode == 0, '断网命令退出码为 0')
        check(server.state.logout_calls == 1, '断网接口被调用 1 次')
        check(server.state.online is False, '模拟服务器状态变为已断网')

        print('\n[5] 检查日志文件')
        log_root = os.path.dirname(CLI_EXE) if CLI_EXE else ROOT
        log_path = os.path.join(log_root, 'logs', 'campus-network-launcher.log')
        check(os.path.isfile(log_path), '日志文件已生成：%s' % log_path)
        if os.path.isfile(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='replace') as fp:
                content = fp.read()
            check('守护模式启动' in content, '日志中包含守护模式启动记录')
            check('认证成功' in content, '日志中包含认证成功记录')

    print('\n' + '=' * 60)
    if FAILURES:
        print('端到端自检未通过，失败项 %d 个：' % len(FAILURES))
        for item in FAILURES:
            print('  - %s' % item)
        return 1
    print('端到端自检全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
