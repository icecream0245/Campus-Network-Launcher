#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面冒烟测试：真实创建 tkinter 窗口但不进入 mainloop。

无桌面会话（例如纯 SSH / 服务环境）时自动跳过。
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip('tkinter')


def _can_create_window() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _can_create_window(), reason='当前环境无法创建图形窗口')


def test_window_smoke(config_factory):
    from ruijie_eporta.gui.app import AppWindow
    from ruijie_eporta.gui.controller import AppController

    cfg = config_factory(extra='notify:\n  toast: false\n')
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)

    try:
        assert window.root.title().startswith('锐捷 ePorta 连接工具')
        window._append_log('测试日志', 'ok')
        window._refresh_dynamic()
        assert window.watch_btn.cget('text') == '开始守护'

        # 触发一次状态刷新（指向不可达地址，会快速失败，不影响测试）
        window._on_refresh()
        window._pump()

        window.show()
        window.hide()
    finally:
        window.quit()

    assert window._closing is True


def test_window_start_watching_flag(config_factory):
    from ruijie_eporta.gui.app import AppWindow
    from ruijie_eporta.gui.controller import AppController

    cfg = config_factory(extra='notify:\n  toast: false\n')
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)

    try:
        window._on_toggle_watch()
        assert controller.watching is True
        window._refresh_dynamic()
        assert window.watch_btn.cget('text') == '停止守护'

        window._on_toggle_watch()
    finally:
        window.quit()

    assert controller.watching is False


def test_gui_available_helper():
    from ruijie_eporta.gui.app import gui_available

    assert gui_available() is True


def test_open_config_reports_notepad_fallback(config_factory, monkeypatch):
    """点击「配置文件」时必须有明确反馈（系统未关联 .yml 也要能打开）。"""

    from ruijie_eporta.gui import app as app_module
    from ruijie_eporta.gui.app import AppWindow
    from ruijie_eporta.gui.controller import AppController

    cfg = config_factory(extra='notify:\n  toast: false\n')
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)

    try:
        monkeypatch.setattr(app_module.winutil, 'open_path', lambda path, **kwargs: 'notepad')
        window._on_open_config()
        text = window.log_text.get('1.0', 'end')
        assert '记事本' in text
        assert '配置文件' in text
    finally:
        window.quit()


def test_open_config_missing_path_is_reported(config_factory, monkeypatch):
    from ruijie_eporta.gui.app import AppWindow
    from ruijie_eporta.gui.controller import AppController

    cfg = config_factory(extra='notify:\n  toast: false\n')
    controller = AppController(cfg)
    window = AppWindow(controller, start_watching=False, start_hidden=True)

    try:
        window._open_path_with_feedback('', '配置文件')
        assert '找不到配置文件' in window.log_text.get('1.0', 'end')
    finally:
        window.quit()
