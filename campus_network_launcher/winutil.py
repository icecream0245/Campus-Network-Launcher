#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 相关的小工具：单实例互斥体、原生消息框。

这里全部使用标准库 ``ctypes``，不依赖 pywin32 / tkinter，
因此打包体积更小、对新版 Python 兼容性更好。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
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
    title: str = 'Campus Network Launcher',
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


def ask_yes_no(text: str, title: str = 'Campus Network Launcher') -> bool:
    """询问式对话框，返回用户是否选择"是"。"""

    return message_box(text, title, MB_YESNO | MB_ICONQUESTION) == IDYES


def acquire_single_instance(name: str = 'campus_network_launcher_tool') -> bool:
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


# 这些后缀在没有系统关联时用记事本兜底打开
TEXT_EDITOR_EXTENSIONS = ('.txt', '.log', '.yml', '.yaml', '.json', '.md', '.ini', '.cfg', '.conf')


def _progid_has_open_command(prog_id: str) -> bool:
    """判断某个 ProgID 是否真的注册了打开命令。"""

    if not prog_id:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r'\shell\open\command') as key:
            return bool(winreg.QueryValueEx(key, '')[0])
    except Exception:
        return False


def has_file_association(path: str) -> bool:
    """判断该文件扩展名在系统里是否有可用的打开方式。

    必须自己查注册表：对于没有关联的扩展名，``os.startfile`` 可能**既不报错也不打开任何东西**
    （或弹出「打开方式」对话框），无法通过异常判断，因此要提前检查。
    """

    if not IS_WINDOWS:
        return True

    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return False

    try:
        import winreg
    except Exception:  # pragma: no cover - 非 Windows
        return True

    # 1) 用户显式选择过的关联优先
    user_choice = r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts' + '\\' + ext.lstrip('.') + r'\UserChoice'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_choice) as key:
            prog_id = str(winreg.QueryValueEx(key, 'ProgId')[0] or '')
        if prog_id:
            return _progid_has_open_command(prog_id) or prog_id.startswith('AppX')
    except Exception:
        pass

    # 2) 系统级关联
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as key:
            prog_id = str(winreg.QueryValueEx(key, '')[0] or '')
        if prog_id and _progid_has_open_command(prog_id):
            return True
    except Exception:
        pass

    # 注意：不能用 HKCR\<ext>\OpenWithProgids 判断——那只是「打开方式」的候选程序列表，
    # 并不代表存在默认程序。本机 .yml 就是这种情况：有候选、无默认程序，
    # os.startfile 会静默失败（既不报错也不打开）。
    return False


def _reveal_in_explorer(target: str) -> str:
    """在资源管理器中定位文件（或打开其所在目录）。"""

    try:
        subprocess.Popen(['explorer.exe', '/select,' + target])
        return 'explorer'
    except Exception as exc:
        log.warning('在资源管理器中定位 %s 失败：%s', target, exc)
        return ''


def open_path(path: str, *, prefer_text_editor: bool = False) -> str:
    """用系统默认程序打开文件或文件夹，失败时逐级回退。

    很多机器上 ``.yml`` / ``.yaml`` 并没有文件关联，此时 ``os.startfile`` 会直接失败，
    因此这里依次尝试：系统默认程序 → 记事本 → 资源管理器定位。

    Args:
        path: 目标文件或目录。
        prefer_text_editor: 为 True 时跳过默认程序，直接用记事本打开。

    Returns:
        str: ``'default'`` / ``'notepad'`` / ``'explorer'``，全部失败时返回 ``''``。
    """

    if not path:
        return ''
    target = os.path.abspath(str(path))

    if not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent and os.path.isdir(parent):
            # 文件不在但目录在：定位到目录，至少让用户看到位置
            return _reveal_in_explorer(parent)
        log.warning('路径不存在，无法打开：%s', target)
        return ''

    if not IS_WINDOWS:
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(['open', target])
            else:
                subprocess.Popen(['xdg-open', target])
            return 'default'
        except Exception as exc:
            log.warning('打开 %s 失败：%s', target, exc)
            return ''

    if not prefer_text_editor:
        if has_file_association(target):
            try:
                os.startfile(target)  # type: ignore[attr-defined]
                return 'default'
            except OSError as exc:
                # 例如扩展名没有关联程序（WinError 1155）
                log.warning('系统默认程序无法打开 %s：%s', target, exc)
        else:
            # 无关联时 os.startfile 可能既不报错也不打开，直接走回退链
            log.info('%s 在该系统上没有可用的文件关联，改用回退方式打开', target)

    if os.path.isfile(target) and target.lower().endswith(TEXT_EDITOR_EXTENSIONS):
        try:
            subprocess.Popen(['notepad.exe', target])
            return 'notepad'
        except Exception as exc:
            log.warning('用记事本打开 %s 失败：%s', target, exc)

    return _reveal_in_explorer(target)


# --------------------------------------------------------------------------- #
# 密码保护：Windows DPAPI（与当前 Windows 用户绑定）
# --------------------------------------------------------------------------- #
DPAPI_PREFIX = 'dpapi:'


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi_crypt(data: bytes, *, protect: bool) -> Optional[bytes]:
    """调用 CryptProtectData / CryptUnprotectData；失败返回 None。"""

    if not IS_WINDOWS:
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        buffer_in = ctypes.create_string_buffer(data, len(data))
        blob_in = _DATA_BLOB(len(data), ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()

        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(blob_in), 'campus-network-launcher', None, None, None, 0, ctypes.byref(blob_out)
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
            )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception as exc:  # pragma: no cover - 极端环境
        log.debug('DPAPI 调用失败：%s', exc)
        return None


def dpapi_available() -> bool:
    """当前环境能否使用 DPAPI（会做一次加解密往返验证）。"""

    probe = b'campus-network-launcher-probe'
    encrypted = _dpapi_crypt(probe, protect=True)
    if not encrypted:
        return False
    return _dpapi_crypt(encrypted, protect=False) == probe


def dpapi_protect(plain: str) -> str:
    """加密文本，返回 ``dpapi:<base64>``；不可用或失败时原样返回（并记录警告）。"""

    import base64

    text = str(plain or '')
    if not text or text.startswith(DPAPI_PREFIX):
        return text
    encrypted = _dpapi_crypt(text.encode('utf-8'), protect=True)
    if not encrypted:
        log.warning('DPAPI 不可用，密码只能以明文保存')
        return text
    return DPAPI_PREFIX + base64.b64encode(encrypted).decode('ascii')


def dpapi_unprotect(token: str) -> str:
    """还原 ``dpapi:<base64>``；非该前缀原样返回（兼容旧明文）。失败返回空串。"""

    import base64

    text = str(token or '')
    if not text.startswith(DPAPI_PREFIX):
        return text
    try:
        raw = base64.b64decode(text[len(DPAPI_PREFIX):])
    except Exception:
        return ''
    plain = _dpapi_crypt(raw, protect=False)
    if plain is None:
        log.warning('DPAPI 解密失败（可能换了 Windows 用户或电脑）')
        return ''
    try:
        return plain.decode('utf-8')
    except UnicodeDecodeError:
        return ''


# --------------------------------------------------------------------------- #
# 系统设置：NCSI 主动探测（决定 Windows 是否自动弹出认证页浏览器）
# --------------------------------------------------------------------------- #
NCSI_REG_PATH = r'SYSTEM\CurrentControlSet\Services\NlaSvc\Parameters\Internet'
NCSI_VALUE = 'EnableActiveProbing'


def active_probing_enabled() -> Optional[bool]:
    """读取 NCSI 主动探测是否启用；读不到返回 None。"""

    if not IS_WINDOWS:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NCSI_REG_PATH) as key:
            value, _kind = winreg.QueryValueEx(key, NCSI_VALUE)
        return bool(int(value))
    except FileNotFoundError:
        return True  # 键不存在时 Windows 默认启用
    except Exception as exc:  # pragma: no cover
        log.debug('读取 NCSI 设置失败：%s', exc)
        return None


def set_active_probing(enabled: bool) -> Tuple[bool, str]:
    """提权修改 NCSI 主动探测开关。

    Args:
        enabled: True 表示恢复（启用探测）；False 表示屏蔽（禁止探测、不再自动弹浏览器）。

    Returns:
        Tuple[bool, str]: (是否成功, 给用户看的说明)
    """

    if not IS_WINDOWS:
        return False, '仅支持 Windows'

    if enabled:
        command = 'reg delete "HKLM\\%s" /v %s /f' % (NCSI_REG_PATH, NCSI_VALUE)
        action = '已恢复开机认证弹窗'
    else:
        command = 'reg add "HKLM\\%s" /v %s /t REG_DWORD /d 0 /f' % (NCSI_REG_PATH, NCSI_VALUE)
        action = '已屏蔽开机认证弹窗'

    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'cmd.exe', '/c ' + command, None, 0)
    except Exception as exc:  # pragma: no cover
        return False, '调用管理员权限失败：%s' % exc

    if int(result) <= 32:
        return False, '未能获取管理员权限（UAC 被取消）。可手动执行：%s' % command

    import time

    expected = bool(enabled)
    for _attempt in range(12):
        time.sleep(0.6)
        current = active_probing_enabled()
        if current is expected:
            return True, '%s（重启电脑后生效）' % action
    return False, '未能确认修改结果（可能取消了 UAC）。可手动执行：%s' % command

