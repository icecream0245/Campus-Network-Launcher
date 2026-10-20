#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统托盘图标（基于 pystray）。

设计要点：

* pystray 不可用（未安装 / 平台不支持）时安静降级，界面照常工作；
* 托盘自带的 Win32 消息循环运行在独立线程中，**不直接操作 tkinter**，
  只通过控制器投递 UI 请求；
* 图标右下角叠加状态圆点：绿色=已联网、红色=未联网、灰色=未知；
* 对受限环境（受管终端 / 沙箱 / 低完整性进程）做兼容：
  pystray 创建窗口时会调用 ``ChangeWindowMessageFilterEx``，
  该调用在受管环境下常被拒绝（WinError 5），会导致托盘创建失败。
  这一步只影响"资源管理器重启后重新注册托盘图标"的消息过滤，
  因此这里将其失败降级为忽略。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from .. import __version__, winutil
from .controller import AppController

log = logging.getLogger(__name__)

APP_TITLE = 'Campus Network Launcher'

# 状态颜色（RGBA）
COLOR_ONLINE = (46, 160, 67, 255)
COLOR_OFFLINE = (218, 54, 51, 255)
COLOR_UNKNOWN = (150, 150, 150, 255)


def status_color(online: Optional[bool]) -> tuple:
    """托盘图像用的 RGBA 颜色。"""

    if online is None:
        return COLOR_UNKNOWN
    return COLOR_ONLINE if online else COLOR_OFFLINE


def status_hex(online: Optional[bool]) -> str:
    """tkinter 控件用的 ``#RRGGBB`` 颜色。"""

    red, green, blue, _alpha = status_color(online)
    return '#%02x%02x%02x' % (red, green, blue)


def icon_file() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'wangluo.ico')


def build_status_image(online: Optional[bool]):
    """在应用图标右下角叠加状态圆点。"""

    from PIL import Image, ImageDraw

    image = Image.open(icon_file()).convert('RGBA')
    size = 64
    image = image.resize((size, size), Image.LANCZOS)

    draw = ImageDraw.Draw(image)
    radius = 16
    margin = 2
    left = size - radius - margin
    draw.ellipse(
        [left, left, size - margin, size - margin],
        fill=status_color(online),
        outline=(255, 255, 255, 255),
        width=2,
    )
    return image


def patch_message_filter() -> str:
    """忽略 pystray 设置窗口消息过滤器时的权限失败。

    Returns:
        str: ``patched`` / ``no-attribute`` / ``unavailable``
    """

    try:
        from pystray._util import win32 as pystray_win32
    except Exception as exc:
        log.debug('无法导入 pystray 内部模块，跳过兼容补丁：%s', exc)
        return 'unavailable'

    original = getattr(pystray_win32, 'ChangeWindowMessageFilterEx', None)
    if original is None:
        return 'no-attribute'

    def safe(*args: Any, **kwargs: Any):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            log.debug('忽略 ChangeWindowMessageFilterEx 失败：%s', exc)
            return None

    pystray_win32.ChangeWindowMessageFilterEx = safe
    return 'patched'


class TrayIcon:
    """托盘图标封装。"""

    def __init__(self, controller: AppController, *, logger: Optional[logging.Logger] = None) -> None:
        self.controller = controller
        self.log = logger or log
        self._icon: Any = None
        self._thread: Optional[threading.Thread] = None
        self.available = False

    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """尝试创建并运行托盘图标；失败返回 False（不影响主界面）。"""

        try:
            import pystray
        except Exception as exc:
            self.log.info('未安装 pystray，跳过系统托盘：%s', exc)
            return False

        patch_message_filter()

        try:
            self._icon = pystray.Icon(
                'campus_network_launcher_tool',
                build_status_image(self.controller.online),
                self.tooltip(),
                menu=self._build_menu(pystray),
            )
        except Exception as exc:
            self.log.warning('创建托盘图标失败：%s', exc)
            return False

        self._thread = threading.Thread(target=self._run, name='eporta-tray', daemon=True)
        self._thread.start()
        self.available = True
        self.log.info('系统托盘已启动')
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:  # pragma: no cover - 平台相关
            self.log.warning('托盘图标运行异常，界面仍可正常使用：%s', exc)
            self.available = False

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception as exc:  # pragma: no cover
            self.log.debug('停止托盘图标失败：%s', exc)
        finally:
            self._icon = None
            self.available = False

    # ------------------------------------------------------------------ #
    def _build_menu(self, pystray):
        controller = self.controller

        def toggle_autostart(_icon=None, _item=None):
            controller.set_autostart(not controller.autostart_enabled())

        return pystray.Menu(
            pystray.MenuItem('显示主界面', lambda _icon, _item: controller.request_show(), default=True),
            pystray.MenuItem(
                lambda _item: '停止守护' if controller.watching else '开始守护',
                lambda _icon, _item: controller.request_toggle_watch(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('立即登录', lambda _icon, _item: controller.login_now()),
            pystray.MenuItem('立即断网', lambda _icon, _item: controller.logout_now()),
            pystray.MenuItem('刷新状态', lambda _icon, _item: controller.refresh_status()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                '开机自启',
                toggle_autostart,
                checked=lambda _item: controller.autostart_enabled(),
            ),
            pystray.MenuItem('打开日志', lambda _icon, _item: winutil.open_path(controller.log_file_path())),
            pystray.MenuItem('打开配置文件', lambda _icon, _item: winutil.open_path(controller.config_path)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', lambda _icon, _item: controller.request_quit()),
        )

    # ------------------------------------------------------------------ #
    def tooltip(self) -> str:
        controller = self.controller
        lines = [
            '%s v%s' % (APP_TITLE, __version__),
            '状态：%s' % controller.state_text(),
            '守护：%s' % ('运行中 %s' % controller.watch_uptime if controller.watching else '已停止'),
            '账号：%s' % (controller.user_id or '-'),
        ]
        return '\n'.join(lines)

    def update(
        self,
        *,
        online: Optional[bool] = None,
        watching: Optional[bool] = None,
        tooltip: Optional[str] = None,
    ) -> None:
        """刷新图标与菜单状态（可从任意线程调用）。"""

        if self._icon is None:
            return
        try:
            self._icon.icon = build_status_image(online if online is not None else self.controller.online)
            self._icon.title = tooltip or self.tooltip()
            self._icon.update_menu()
        except Exception as exc:  # pragma: no cover
            self.log.debug('刷新托盘图标失败：%s', exc)
