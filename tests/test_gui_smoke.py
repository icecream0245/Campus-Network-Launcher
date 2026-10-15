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
