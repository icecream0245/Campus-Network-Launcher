#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通知模块：Windows 通知中心消息 + 原生消息框 + 日志兜底。

上游依赖 GitHub 上的 ``tinyWinToast``（git 依赖，安装和打包都不稳定），
这里改为直接用 PowerShell 调用 WinRT 的 Toast API，去掉该依赖；
消息框用 ``ctypes`` 调用 ``MessageBoxW``，因此不再需要 tkinter。
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from . import winutil

log = logging.getLogger(__name__)

APP_TITLE = '锐捷 ePorta 连接工具'
# 借用 PowerShell 已注册的 AppID，无需安装/注册自己的快捷方式即可显示通知
TOAST_APP_ID = r'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

_TOAST_SCRIPT = """\
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
{xml}
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def build_toast_xml(title: str, message: str, attribution: str = APP_TITLE) -> str:
    """生成 Toast XML（文本全部转义，避免注入）。"""

    return (
        '<toast duration="short">'
        '<visual><binding template="ToastGeneric">'
        '<text>%s</text><text>%s</text>'
        '<text placement="attribution">%s</text>'
        '</binding></visual>'
        '</toast>' % (xml_escape(title), xml_escape(message), xml_escape(attribution))
    )


class Notifier:
    """统一的通知出口。

    Args:
        toast: 是否使用 Windows 通知中心消息。
        dialog: 是否额外弹原生消息框（守护模式下建议关闭）。
        quiet: 静默模式，仅通知错误。
    """

    def __init__(
        self,
        *,
        toast: bool = True,
        dialog: bool = False,
        quiet: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.toast = bool(toast)
        self.dialog = bool(dialog)
        self.quiet = bool(quiet)
        self.log = logger or log

    # ------------------------------------------------------------------ #
    def info(self, title: str, message: str = '') -> None:
        if self.quiet:
            self.log.info('[通知-静默] %s: %s', title, message)
            return
        self._dispatch(title, message, winutil.MB_ICONINFORMATION)

    def warn(self, title: str, message: str = '') -> None:
        if self.quiet:
            self.log.warning('[通知-静默] %s: %s', title, message)
            return
        self._dispatch(title, message, winutil.MB_ICONWARNING)

    def error(self, title: str, message: str = '') -> None:
        self._dispatch(title, message, winutil.MB_ICONERROR)

    # ------------------------------------------------------------------ #
    def _dispatch(self, title: str, message: str, icon_flag: int) -> None:
        self.log.info('[通知] %s: %s', title, message)
        sent = False
        if self.toast:
            sent = self.show_toast(title, message)
        if self.dialog:
            winutil.message_box(message, title, winutil.MB_OK | icon_flag)
        elif not sent and self.toast:
            # Toast 不可用（例如精简版系统）时，至少给用户一个可见提示
            self.log.debug('Toast 发送失败，跳过弹窗（dialog=false）')

    def show_toast(self, title: str, message: str) -> bool:
        """发送 Windows Toast 通知，返回是否成功。"""

        if not winutil.IS_WINDOWS:
            return False
        script = _TOAST_SCRIPT.format(xml=build_toast_xml(title, message), app_id=TOAST_APP_ID)
        output = winutil.run_powershell(script, timeout=15)
        if output is None:
            self.log.debug('Toast 通知发送失败')
            return False
        return True


def icon_path() -> str:
    """返回内置图标路径。"""

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'wangluo.ico')
