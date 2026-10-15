#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 相关的小工具：单实例互斥体、原生消息框。

这里全部使用标准库 ``ctypes``，不依赖 pywin32 / tkinter，
因此打包体积更小、对新版 Python 兼容性更好。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import List, Optional

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith('win')

MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000

IDOK = 1
IDCANCEL = 2
IDYES = 6
IDNO = 7

ERROR_ALREADY_EXISTS = 183

# 保持句柄引用，避免互斥体被垃圾回收提前释放
_MUTEX_HANDLES: List[int] = []


def message_box(
    text: str,
    title: str = '锐捷 ePorta 连接工具',
    flags: int = MB_OK | MB_ICONINFORMATION,
) -> int:
    """弹出原生消息框；非 Windows 平台直接返回 IDOK 并写日志。"""

    if not IS_WINDOWS:
        log.info('[消息框] %s: %s', title, text)
        return IDOK
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, str(text), str(title), int(flags) | MB_TOPMOST))
    except Exception as exc:  # pragma: no cover - 极少见
        log.warning('弹出消息框失败：%s', exc)
        return IDOK


def ask_yes_no(text: str, title: str = '锐捷 ePorta 连接工具') -> bool:
    """询问式对话框，返回用户是否选择"是"。"""

    return message_box(text, title, MB_YESNO | MB_ICONQUESTION) == IDYES


def acquire_single_instance(name: str = 'ruijie_eporta_tool') -> bool:
    """获取单实例锁。

    Returns:
        bool: True 表示获得锁（当前是唯一实例）；False 表示已有实例在运行。
    """

    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, 0, name)
        last_error = ctypes.get_last_error()
        if handle:
            _MUTEX_HANDLES.append(handle)
        return last_error != ERROR_ALREADY_EXISTS
    except Exception as exc:  # pragma: no cover - 非预期环境
        log.warning('创建互斥体失败，跳过单实例检查：%s', exc)
        return True


def run_powershell(script: str, timeout: float = 20.0, hidden: bool = True) -> Optional[str]:
    """执行一段 PowerShell 脚本（用 EncodedCommand 传入，避免引号转义问题）。

    Returns:
        Optional[str]: 成功时返回 stdout 文本，失败返回 None。
    """

    import base64
    import subprocess

    if not IS_WINDOWS:
        return None

    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    startupinfo = None
    if hidden and hasattr(subprocess, 'STARTUPINFO'):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            capture_output=True,
            timeout=timeout,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except Exception as exc:
        log.warning('调用 PowerShell 失败：%s', exc)
        return None

    if completed.returncode != 0:
        stderr = completed.stderr.decode('utf-8', errors='replace').strip()
        log.debug('PowerShell 返回码 %s：%s', completed.returncode, stderr)
        return None
    return completed.stdout.decode('utf-8', errors='replace')
