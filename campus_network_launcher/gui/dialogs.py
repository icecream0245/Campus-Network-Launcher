#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次运行向导与服务商选择弹窗。

两个对话框都只负责交互，**不直接读写配置文件**：
保存动作通过回调交给控制器完成，这样业务逻辑可以单独测试。
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, List, Optional, Sequence

log = logging.getLogger(__name__)

FONT_FAMILY = 'Microsoft YaHei UI'

# 抓不到真实列表时的兜底（锐捷 ePortal 常见运营商）
FALLBACK_SERVICES = ('教师免费', '学生免费', '中国电信', '中国移动', '中国联通')

# on_save(user_id, password, service, options)
ProfileCallback = Callable[[str, str, str, List[str]], None]
# on_fetch_services(done_callback)，done_callback(services, error)
ServicesFetcher = Callable[[Callable[[List[str], str], None]], None]
# on_confirm(service)
ServiceCallback = Callable[[str], None]


def _center_on(parent: tk.Misc, window: tk.Toplevel, width: int, height: int) -> None:
    window.update_idletasks()
    try:
        x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
        y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 3, 0)
    except tk.TclError:  # pragma: no cover - 极端情况
        x = y = 200
    window.geometry('%dx%d+%d+%d' % (width, height, x, y))


class SetupWizard(tk.Toplevel):
    """首次使用：填写学号、密码并选择服务商。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        user_id: str = '',
        password: str = '',
        service: str = '',
        services: Optional[Sequence[str]] = None,
        on_save: ProfileCallback,
        on_fetch_services: Optional[ServicesFetcher] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(master)
        self.log = logger or log
        self._save_cb = on_save
        self._fetch_cb = on_fetch_services
        self.result: Optional[tuple] = None

        self.title('首次使用设置 - Campus Network Launcher')
        self.resizable(False, False)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)

        options = [str(item).strip() for item in (services or []) if str(item or '').strip()] or list(FALLBACK_SERVICES)
        self.user_var = tk.StringVar(value=user_id)
        self.password_var = tk.StringVar(value=password)
        self.service_var = tk.StringVar(value=(service or options[0]).strip())
        self.show_password_var = tk.BooleanVar(value=False)
        self.hint_var = tk.StringVar(
            value='点「一键执行」会保存配置、自动重启程序并完成认证（可能需要短暂断网）'
        )

        self._build(options)
        _center_on(master, self, 480, 300)
        self.grab_set()
        self.user_entry.focus_set()

    # ------------------------------------------------------------------ #
    def _build(self, options: Sequence[str]) -> None:
        body = ttk.Frame(self, padding=(18, 16, 18, 10))
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(
            body,
            text='第一次使用，请填写你的校园网账号',
            font=(FONT_FAMILY, 11, 'bold'),
        ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))

        ttk.Label(body, text='学号：', font=(FONT_FAMILY, 9)).grid(row=1, column=0, sticky='e', pady=4)
        self.user_entry = ttk.Entry(body, textvariable=self.user_var, width=30, font=(FONT_FAMILY, 9))
        self.user_entry.grid(row=1, column=1, sticky='w', pady=4)

        ttk.Label(body, text='密码：', font=(FONT_FAMILY, 9)).grid(row=2, column=0, sticky='e', pady=4)
        self.password_entry = ttk.Entry(
            body, textvariable=self.password_var, width=30, font=(FONT_FAMILY, 9), show='*'
        )
        self.password_entry.grid(row=2, column=1, sticky='w', pady=4)

        ttk.Label(body, text='服务商：', font=(FONT_FAMILY, 9)).grid(row=3, column=0, sticky='e', pady=4)
        combo_row = ttk.Frame(body)
        combo_row.grid(row=3, column=1, sticky='w', pady=4)
        self.service_combo = ttk.Combobox(
            combo_row, textvariable=self.service_var, width=22, values=list(options), font=(FONT_FAMILY, 9)
        )
        self.service_combo.pack(side='left')
        ttk.Button(combo_row, text='刷新列表', width=10, command=self._fetch_services).pack(side='left', padx=(6, 0))

        ttk.Checkbutton(
            body,
            text='显示密码',
            variable=self.show_password_var,
            command=self._toggle_password,
        ).grid(row=4, column=1, sticky='w', pady=(0, 2))

        ttk.Label(
            body,
            textvariable=self.hint_var,
            foreground='#b7791f',
            wraplength=410,
            justify='left',
        ).grid(row=5, column=0, columnspan=2, sticky='w', pady=(6, 0))

        buttons = ttk.Frame(self, padding=(18, 0, 18, 14))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='一键执行', width=14, command=self._on_confirm).pack(side='right')
        ttk.Button(buttons, text='稍后', width=10, command=self._on_cancel).pack(side='right', padx=(0, 8))

    # ------------------------------------------------------------------ #
    def _toggle_password(self) -> None:
        self.password_entry.configure(show='' if self.show_password_var.get() else '*')

    def _fetch_services(self) -> None:
        if self._fetch_cb is None:
            self.hint_var.set('当前环境无法自动获取，请手动填写服务商名称')
            return
        self.hint_var.set('正在获取服务商列表…（需要处于未认证状态）')
        try:
            self._fetch_cb(self.apply_services)
        except Exception as exc:  # pragma: no cover - 回调异常
            self.hint_var.set('获取失败：%s' % exc)

    def apply_services(self, services: Sequence[str], error: str = '') -> None:
        """由后台线程通过主线程回调把结果填入下拉框。"""

        cleaned = [str(item).strip() for item in (services or []) if str(item or '').strip()]
        if cleaned:
            self.service_combo['values'] = cleaned
            if not self.service_var.get().strip():
                self.service_var.set(cleaned[0])
            self.hint_var.set('已获取 %d 个服务商：%s' % (len(cleaned), '、'.join(cleaned[:6])))
        else:
            self.hint_var.set('获取失败：%s（可直接手动填写）' % (error or '未知原因'))

    # ------------------------------------------------------------------ #
    def _on_confirm(self) -> None:
        user_id = self.user_var.get().strip()
        password = self.password_var.get()
        service = self.service_var.get().strip()

        if not user_id:
            messagebox.showwarning('请填写学号', '学号不能为空。', parent=self)
            self.user_entry.focus_set()
            return
        if not password:
            messagebox.showwarning('请填写密码', '密码不能为空。', parent=self)
            self.password_entry.focus_set()
            return

        options = [str(item) for item in (self.service_combo['values'] or []) if str(item).strip()]
        self.result = (user_id, password, service, options)
        try:
            self._save_cb(user_id, password, service, options)
        finally:
            self._close()

    def _on_cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:  # pragma: no cover
            pass
        self.destroy()

    def wait(self) -> Optional[tuple]:
        """模态等待用户操作，返回 (学号, 密码, 服务商, 列表) 或 None。"""

        self.wait_window(self)
        return self.result


class ServicePicker(tk.Toplevel):
    """登录失败时确认或更改服务商。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        current: str = '',
        services: Optional[Sequence[str]] = None,
        reason: str = '',
        on_confirm: ServiceCallback,
        on_fetch_services: Optional[ServicesFetcher] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(master)
        self.log = logger or log
        self._confirm_cb = on_confirm
        self._fetch_cb = on_fetch_services
        self.result: Optional[str] = None

        self.title('确认服务商 - Campus Network Launcher')
        self.resizable(False, False)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)

        options = [str(item).strip() for item in (services or []) if str(item or '').strip()] or list(FALLBACK_SERVICES)
        self.service_var = tk.StringVar(value=(current or options[0]).strip())
        self.hint_var = tk.StringVar(value='')

        self._build(options, reason)
        _center_on(master, self, 460, 250)
        self.grab_set()

    def _build(self, options: Sequence[str], reason: str) -> None:
        body = ttk.Frame(self, padding=(18, 16, 18, 10))
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(
            body,
            text='连接失败，可能是服务商选择不正确',
            font=(FONT_FAMILY, 11, 'bold'),
            foreground='#c0392b',
        ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))

        ttk.Label(
            body,
            text=('失败原因：%s' % (reason or '未知')) if reason else '',
            font=(FONT_FAMILY, 9),
            foreground='#555555',
            wraplength=400,
            justify='left',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 10))

        ttk.Label(body, text='当前服务商：', font=(FONT_FAMILY, 9)).grid(row=2, column=0, sticky='e', pady=4)
        combo_row = ttk.Frame(body)
        combo_row.grid(row=2, column=1, sticky='w', pady=4)
        self.service_combo = ttk.Combobox(
            combo_row, textvariable=self.service_var, width=22, values=list(options), font=(FONT_FAMILY, 9)
        )
        self.service_combo.pack(side='left')
        ttk.Button(combo_row, text='刷新列表', width=10, command=self._fetch_services).pack(side='left', padx=(6, 0))

        ttk.Label(
            body,
            textvariable=self.hint_var,
            foreground='#b7791f',
            wraplength=410,
            justify='left',
        ).grid(row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))

        buttons = ttk.Frame(self, padding=(18, 0, 18, 14))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='使用这个服务商', width=16, command=self._on_confirm).pack(side='right')
        ttk.Button(buttons, text='取消', width=10, command=self._on_cancel).pack(side='right', padx=(0, 8))

    # ------------------------------------------------------------------ #
    def _fetch_services(self) -> None:
        if self._fetch_cb is None:
            self.hint_var.set('当前环境无法自动获取，请手动填写')
            return
        self.hint_var.set('正在获取服务商列表…（需要处于未认证状态）')
        try:
            self._fetch_cb(self.apply_services)
        except Exception as exc:  # pragma: no cover
            self.hint_var.set('获取失败：%s' % exc)

    def apply_services(self, services: Sequence[str], error: str = '') -> None:
        cleaned = [str(item).strip() for item in (services or []) if str(item or '').strip()]
        if cleaned:
            self.service_combo['values'] = cleaned
            self.hint_var.set('已获取 %d 个服务商' % len(cleaned))
        else:
            self.hint_var.set('获取失败：%s（可直接手动填写）' % (error or '未知原因'))

    def _on_confirm(self) -> None:
        service = self.service_var.get().strip()
        if not service:
            messagebox.showwarning('请选择服务商', '服务商不能为空。', parent=self)
            return
        self.result = service
        try:
            self._confirm_cb(service)
        finally:
            self._close()

    def _on_cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:  # pragma: no cover
            pass
        self.destroy()

    def wait(self) -> Optional[str]:
        self.wait_window(self)
        return self.result
