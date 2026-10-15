#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自启管理。

支持两种方式：

* ``startup``（默认）：在用户启动目录创建快捷方式，与上游 README 的手工做法一致；
* ``registry``：写入 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``。

两种方式都只影响当前用户，不需要管理员权限。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Tuple

from . import config, winutil

log = logging.getLogger(__name__)

APP_NAME = 'Ruijie_ePorta_Tool'
SHORTCUT_NAME = 'Ruijie ePorta Tool.lnk'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
DEFAULT_ARGS = '--watch --quiet'


def startup_dir() -> str:
    """返回当前用户的「启动」文件夹路径。"""

    appdata = os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Roaming')
    return os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')


def shortcut_path() -> str:
    return os.path.join(startup_dir(), SHORTCUT_NAME)


def _pythonw() -> str:
    exe = sys.executable or 'python.exe'
    candidate = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return candidate if os.path.isfile(candidate) else exe


def launch_target(args: str = DEFAULT_ARGS) -> Tuple[str, str, str]:
    """返回自启项的 (目标程序, 参数, 工作目录)。"""

    if getattr(sys, 'frozen', False):
        target = os.path.abspath(sys.executable)
        return target, args, os.path.dirname(target)
    return _pythonw(), ('-m ruijie_eporta %s' % args).strip(), config.program_dir()


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def shortcut_icon() -> str:
    """快捷方式使用的图标路径。

    打包后直接指向 exe 自身（版本资源里已嵌入图标，且路径长期有效）；
    源码运行时使用包内的 ico 文件。
    """

    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'wangluo.ico')


def install(method: str = 'startup', *, args: str = DEFAULT_ARGS, name: str = APP_NAME) -> str:
    """安装开机自启，返回描述信息（如安装位置）。"""

    target, arguments, workdir = launch_target(args)

    if method == 'registry':
        return _install_registry(target, arguments, name)
    return _install_startup(target, arguments, workdir, shortcut_icon())


def _install_startup(target: str, arguments: str, workdir: str, icon: str) -> str:
    if not winutil.IS_WINDOWS:
        raise RuntimeError('开机自启仅支持 Windows')

    path = shortcut_path()
    script = """
$ErrorActionPreference = 'Stop'
$dir = {startup_dir}
if (-not (Test-Path -LiteralPath $dir)) {{ New-Item -ItemType Directory -Path $dir -Force | Out-Null }}
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut({path})
$lnk.TargetPath = {target}
$lnk.Arguments = {arguments}
$lnk.WorkingDirectory = {workdir}
$lnk.Description = '锐捷 ePorta 自动登录（守护模式）'
$lnk.IconLocation = {icon}
$lnk.Save()
Write-Output 'ok'
""".format(
        startup_dir=_ps_quote(startup_dir()),
        path=_ps_quote(path),
        target=_ps_quote(target),
        arguments=_ps_quote(arguments),
        workdir=_ps_quote(workdir),
        icon=_ps_quote(icon or target),
    )

    output = winutil.run_powershell(script, timeout=30)
    if output is None:
        raise RuntimeError('创建启动快捷方式失败，请检查 PowerShell 是否可用')

    log.info('已创建开机自启快捷方式：%s', path)
    return path


def _install_registry(target: str, arguments: str, name: str) -> str:
    if not winutil.IS_WINDOWS:
        raise RuntimeError('开机自启仅支持 Windows')
    import winreg

    command = '"%s" %s' % (target, arguments)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    log.info('已写入注册表自启项：HKCU\\%s\\%s', RUN_KEY, name)
    return 'HKCU\\%s\\%s' % (RUN_KEY, name)


def uninstall(method: str = 'startup', *, name: str = APP_NAME) -> bool:
    """卸载开机自启，返回是否确实移除了内容。"""

    if method == 'registry':
        return _uninstall_registry(name)
    return _uninstall_startup()


def _uninstall_startup() -> bool:
    path = shortcut_path()
    if os.path.isfile(path):
        os.remove(path)
        log.info('已删除开机自启快捷方式：%s', path)
        return True
    log.info('未找到开机自启快捷方式：%s', path)
    return False


def _uninstall_registry(name: str) -> bool:
    if not winutil.IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        log.info('已删除注册表自启项：%s', name)
        return True
    except FileNotFoundError:
        log.info('注册表中没有自启项：%s', name)
        return False
    except OSError as exc:  # pragma: no cover
        log.warning('删除注册表自启项失败：%s', exc)
        return False


def is_installed(method: str = 'startup', *, name: str = APP_NAME) -> bool:
    if method == 'registry':
        if not winutil.IS_WINDOWS:
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, name)
            return True
        except OSError:
            return False
    return os.path.isfile(shortcut_path())


def describe(method: str = 'startup', *, name: str = APP_NAME) -> str:
    """返回当前自启状态的文字描述。"""

    target, arguments, _ = launch_target()
    where = 'HKCU\\%s\\%s' % (RUN_KEY, name) if method == 'registry' else shortcut_path()
    state = '已启用' if is_installed(method, name=name) else '未启用'
    return '%s（%s）\n  位置：%s\n  命令：%s %s' % (state, method, where, target, arguments)


def current_method(cfg: Optional[dict] = None) -> str:
    """从配置中读取自启方式，缺省为 startup。"""

    if cfg:
        method = str((cfg.get('autostart') or {}).get('method') or 'startup').strip().lower()
        if method in ('startup', 'registry'):
            return method
    return 'startup'


def current_args(cfg: Optional[dict] = None) -> str:
    if cfg:
        args = str((cfg.get('autostart') or {}).get('args') or DEFAULT_ARGS).strip()
        if args:
            return args
    return DEFAULT_ARGS
