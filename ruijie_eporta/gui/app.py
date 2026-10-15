#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tkinter 主窗口。

界面只做三件事：**显示状态**、**触发动作**、**展示日志**。
所有耗时操作（认证、探测、自启设置）都交给控制器在后台线程执行，
主线程只通过 ``after()`` 轮询事件队列刷新控件，因此界面不会卡死。
"""

from __future__ import annotations

import logging
import os
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

from .. import __version__, winutil
from ..notify import icon_path
from .controller import (
    UI_LOGIN,
    UI_LOGOUT,
    UI_QUIT,
    UI_SHOW,
    UI_TOGGLE_WATCH,
    AppController,
    UiEvent,
    summarize_probes,
)
from .tray import TrayIcon, status_hex

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 200
TRAY_REFRESH_SECONDS = 2.0
MAX_LOG_LINES = 400
FONT_FAMILY = 'Microsoft YaHei UI'

TAG_COLORS = {
    'ok': '#1a7f37',
    'error': '#c0392b',
    'warn': '#b7791f',
    'info': '#333333',
}

EVENT_TAGS = {
    'login_ok': 'ok',
    'logout_ok': 'ok',
    'login_fail': 'error',
    'logout_fail': 'error',
    'error': 'error',
    'watch_stopped': 'warn',
}


class AppWindow:
    """主窗口。"""

    def __init__(
        self,
        controller: AppController,
        *,
        tray: Optional[TrayIcon] = None,
        logger: Optional[logging.Logger] = None,
        start_watching: bool = True,
        start_hidden: bool = False,
    ) -> None:
        self.controller = controller
        self.tray = tray
        self.log = logger or log
        self._closing = False
        self._tray_hint_shown = False
        self._last_tray_update = 0.0

        self.root = tk.Tk()
        self.root.title('锐捷 ePorta 连接工具 v%s' % __version__)
        self.root.minsize(620, 520)
        self._apply_icon()
        self._build_style()
        self._build()
        self._center(700, 620)

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._append_log('界面已启动，配置文件：%s' % (self.controller.config_path or '-'))

        if start_hidden:
            self.root.withdraw()
            self.log.info('界面以隐藏方式启动（托盘常驻）')

        if start_watching:
            if self.controller.start_watch():
                self._append_log('已自动开始守护模式（掉线会自动重连）', 'ok')
            else:
                self._append_log('守护模式已在运行', 'warn')

        self.root.after(POLL_INTERVAL_MS, self._pump)
        self.root.after(150, self.controller.refresh_status)

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _apply_icon(self) -> None:
        try:
            self.root.iconbitmap(icon_path())
        except Exception as exc:  # pragma: no cover - 平台相关
            self.log.debug('设置窗口图标失败：%s', exc)

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use('vista')
        except tk.TclError:  # pragma: no cover - 非 Windows 主题
            pass
        style.configure('Title.TLabel', font=(FONT_FAMILY, 16, 'bold'))
        style.configure('Info.TLabel', font=(FONT_FAMILY, 9))
        style.configure('Key.TLabel', font=(FONT_FAMILY, 9), foreground='#666666')
        style.configure('Hint.TLabel', font=(FONT_FAMILY, 8), foreground='#777777')
        style.configure('TButton', font=(FONT_FAMILY, 9))
        style.configure('TCheckbutton', font=(FONT_FAMILY, 9))
        style.configure('TLabelframe.Label', font=(FONT_FAMILY, 9))

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        self._build_status_card()
        self._build_info_panel()
        self._build_actions()
        self._build_log_panel()
        self._build_statusbar()

    def _build_status_card(self) -> None:
        card = ttk.Frame(self.root, padding=(16, 14, 16, 6))
        card.grid(row=0, column=0, sticky='ew')
        card.columnconfigure(1, weight=1)

        self.dot = tk.Canvas(card, width=22, height=22, highlightthickness=0)
        self.dot.grid(row=0, column=0, rowspan=2, sticky='w', padx=(0, 10))
        self.dot_item = self.dot.create_oval(3, 3, 19, 19, fill=status_hex(None), outline='')

        self.status_label = ttk.Label(card, text='正在检测…', style='Title.TLabel')
        self.status_label.grid(row=0, column=1, sticky='w')

        self.last_label = ttk.Label(card, text='最近事件：就绪', style='Info.TLabel', foreground='#555555')
        self.last_label.grid(row=1, column=1, sticky='w', pady=(2, 0))

    def _build_info_panel(self) -> None:
        panel = ttk.Frame(self.root, padding=(16, 0, 16, 6))
        panel.grid(row=1, column=0, sticky='ew')
        panel.columnconfigure(1, weight=1)

        self.server_var = tk.StringVar(value=self.controller.server)
        self.user_var = tk.StringVar(value=self.controller.user_id or '-')
        self.watch_var = tk.StringVar(value='守护模式：已停止')
        self.probe_var = tk.StringVar(value=summarize_probes(self.controller.daemon.probes))

        rows = [
            ('认证服务器', self.server_var),
            ('账号', self.user_var),
            ('运行状态', self.watch_var),
            ('探测地址', self.probe_var),
        ]
        for index, (label, var) in enumerate(rows):
            ttk.Label(panel, text=label + '：', style='Key.TLabel').grid(row=index, column=0, sticky='nw', pady=1)
            ttk.Label(panel, textvariable=var, style='Info.TLabel', wraplength=520, justify='left').grid(
                row=index, column=1, sticky='w', pady=1
            )

    def _build_actions(self) -> None:
        panel = ttk.Frame(self.root, padding=(16, 4, 16, 8))
        panel.grid(row=2, column=0, sticky='ew')

        self.login_btn = ttk.Button(panel, text='立即登录', width=12, command=self._on_login)
        self.login_btn.grid(row=0, column=0, padx=(0, 8), pady=3)

        self.logout_btn = ttk.Button(panel, text='立即断网', width=12, command=self._on_logout)
        self.logout_btn.grid(row=0, column=1, padx=(0, 8), pady=3)

        self.refresh_btn = ttk.Button(panel, text='刷新状态', width=12, command=self._on_refresh)
        self.refresh_btn.grid(row=0, column=2, padx=(0, 8), pady=3)

        self.watch_btn = ttk.Button(panel, text='开始守护', width=12, command=self._on_toggle_watch)
        self.watch_btn.grid(row=1, column=0, padx=(0, 8), pady=3)

        self.autostart_var = tk.BooleanVar(value=self.controller.autostart_enabled())
        self.autostart_check = ttk.Checkbutton(
            panel,
            text='开机自启',
            variable=self.autostart_var,
            command=self._on_toggle_autostart,
        )
        self.autostart_check.grid(row=1, column=1, sticky='w', padx=(0, 8), pady=3)

        ttk.Button(panel, text='打开日志', width=12, command=self._on_open_log).grid(
            row=1, column=2, padx=(0, 8), pady=3
        )
        ttk.Button(panel, text='配置文件', width=12, command=self._on_open_config).grid(
            row=1, column=3, padx=(0, 8), pady=3
        )

    def _build_log_panel(self) -> None:
        frame = ttk.LabelFrame(self.root, text='运行日志', padding=(8, 6))
        frame.grid(row=3, column=0, sticky='nsew', padx=16, pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            frame,
            height=10,
            wrap='word',
            state='disabled',
            font=(FONT_FAMILY, 9),
            background='#fcfcfc',
            relief='flat',
            padx=6,
            pady=4,
        )
        self.log_text.grid(row=0, column=0, sticky='nsew')

        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=scrollbar.set)

        for tag, color in TAG_COLORS.items():
            self.log_text.tag_configure(tag, foreground=color)

    def _build_statusbar(self) -> None:
        self.statusbar = ttk.Label(self.root, text='', style='Hint.TLabel', anchor='w', padding=(16, 2, 16, 8))
        self.statusbar.grid(row=4, column=0, sticky='ew')
        self.statusbar.configure(text='日志：%s' % self.controller.log_file_path())

    def _center(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(int((screen_w - width) / 2), 0)
        y = max(int((screen_h - height) / 3), 0)
        self.root.geometry('%dx%d+%d+%d' % (width, height, x, y))

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _append_log(self, text: str, tag: str = 'info') -> None:
        if self._closing:
            return
        line = '[%s] %s\n' % (time.strftime('%H:%M:%S'), text)
        self.log_text.configure(state='normal')
        self.log_text.insert('end', line, tag)
        total_lines = int(self.log_text.index('end-1c').split('.')[0])
        if total_lines > MAX_LOG_LINES:
            self.log_text.delete('1.0', '%d.0' % (total_lines - MAX_LOG_LINES))
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    # ------------------------------------------------------------------ #
    # 事件循环
    # ------------------------------------------------------------------ #
    def _pump(self) -> None:
        if self._closing:
            return
        while True:
            request = self.controller.poll_ui_request()
            if request is None:
                break
            self._handle_ui_request(request)

        while True:
            event = self.controller.poll_event()
            if event is None:
                break
            self._handle_event(event)

        self._refresh_dynamic()
        self.root.after(POLL_INTERVAL_MS, self._pump)

    def _handle_ui_request(self, request: str) -> None:
        if request == UI_SHOW:
            self.show()
        elif request == UI_QUIT:
            self.quit()
        elif request == UI_TOGGLE_WATCH:
            self._on_toggle_watch()
        elif request == UI_LOGIN:
            self._on_login()
        elif request == UI_LOGOUT:
            self._on_logout()

    def _handle_event(self, event: UiEvent) -> None:
        message = event.message or event.kind
        if event.kind == 'state':
            tag = 'ok' if event.data.get('online') else 'warn'
        elif event.kind == 'status':
            detail = event.data.get('online_detail') or ''
            message = '状态已刷新：%s（%s）' % ('已联网' if event.data.get('online') else '未联网', detail)
            tag = 'ok' if event.data.get('online') else 'warn'
        else:
            tag = EVENT_TAGS.get(event.kind, 'info')

        if event.kind == 'keepalive':
            # 保活探测比较频繁，不写入日志区，避免刷屏
            return
        self._append_log(message, tag)

    def _refresh_dynamic(self) -> None:
        controller = self.controller

        self.status_label.configure(text=controller.state_text())
        self.dot.itemconfigure(self.dot_item, fill=status_hex(controller.online))
        self.last_label.configure(text='最近事件：%s' % controller.last_message)

        if controller.watching:
            self.watch_var.set('守护模式：运行中（已持续 %s）' % controller.watch_uptime)
        else:
            self.watch_var.set('守护模式：已停止')
        self.watch_btn.configure(text='停止守护' if controller.watching else '开始守护')

        busy = controller.busy
        button_state = 'disabled' if busy else 'normal'
        self.login_btn.configure(state=button_state)
        self.logout_btn.configure(state=button_state)
        self.refresh_btn.configure(state=button_state)

        now = time.time()
        if self.tray is not None and self.tray.available and now - self._last_tray_update >= TRAY_REFRESH_SECONDS:
            self._last_tray_update = now
            self.tray.update(online=controller.online, watching=controller.watching)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _on_login(self) -> None:
        if not self.controller.login_now():
            self._append_log('已有任务在执行，请稍候…', 'warn')

    def _on_logout(self) -> None:
        if not self.controller.logout_now():
            self._append_log('已有任务在执行，请稍候…', 'warn')

    def _on_refresh(self) -> None:
        if not self.controller.refresh_status():
            self._append_log('已有任务在执行，请稍候…', 'warn')

    def _on_toggle_watch(self) -> None:
        if self.controller.watching:
            self._append_log('正在停止守护模式…')
            self.controller.stop_watch()
        else:
            self.controller.start_watch()

    def _on_toggle_autostart(self) -> None:
        want = bool(self.autostart_var.get())
        self.root.configure(cursor='watch')
        self.root.update_idletasks()
        try:
            message = self.controller.set_autostart(want)
        finally:
            self.root.configure(cursor='')
        self._append_log(message.replace('\n', ' '), 'ok' if want else 'info')
        self.autostart_var.set(self.controller.autostart_enabled())

    def _on_open_log(self) -> None:
        path = self.controller.log_file_path()
        if not winutil.open_path(path):
            self._append_log('日志文件尚未生成：%s' % path, 'warn')

    def _on_open_config(self) -> None:
        path = self.controller.config_path
        if not winutil.open_path(path):
            self._append_log('无法打开配置文件：%s' % path, 'warn')

    # ------------------------------------------------------------------ #
    # 窗口生命周期
    # ------------------------------------------------------------------ #
    def show(self) -> None:
        try:
            self.root.deiconify()
            self.root.state('normal')
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:  # pragma: no cover
            pass

    def hide(self) -> None:
        try:
            self.root.withdraw()
        except tk.TclError:  # pragma: no cover
            pass

    def _on_close(self) -> None:
        if self.tray is not None and self.tray.available:
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._append_log('窗口已隐藏到系统托盘：单击托盘图标可重新打开，托盘菜单里可以退出程序。')
        else:
            self.quit()

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._append_log('正在退出…')
        try:
            self.controller.shutdown()
        finally:
            if self.tray is not None:
                self.tray.stop()
            try:
                self.root.destroy()
            except tk.TclError:  # pragma: no cover
                pass

    def run(self) -> None:
        self.root.mainloop()


def run_gui(
    cfg,
    *,
    controller: Optional[AppController] = None,
    logger: Optional[logging.Logger] = None,
    start_watching: bool = True,
    start_hidden: bool = False,
    enable_tray: bool = True,
) -> int:
    """启动图形界面；返回进程退出码。"""

    controller = controller or AppController(cfg, logger=logger)

    tray: Optional[TrayIcon] = None
    if enable_tray:
        candidate = TrayIcon(controller, logger=logger)
        if candidate.start():
            tray = candidate

    window = AppWindow(
        controller,
        tray=tray,
        logger=logger,
        start_watching=start_watching,
        start_hidden=start_hidden,
    )
    try:
        window.run()
    finally:
        if tray is not None:
            tray.stop()
        controller.shutdown()
    return 0


def gui_available() -> bool:
    """当前环境能否创建窗口（无桌面会话时返回 False）。"""

    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception as exc:
        log.info('当前环境无法创建图形界面：%s', exc)
        return False
