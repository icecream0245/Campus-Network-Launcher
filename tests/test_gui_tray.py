#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统托盘测试：图标生成与依赖缺失时的降级行为（不真的显示托盘）。"""

from __future__ import annotations

import sys

from ruijie_eporta.gui.controller import AppController
from ruijie_eporta.gui.tray import (
    COLOR_OFFLINE,
    COLOR_ONLINE,
    COLOR_UNKNOWN,
    TrayIcon,
    build_status_image,
    status_color,
)


def test_status_color_mapping():
    assert status_color(True) == COLOR_ONLINE
    assert status_color(False) == COLOR_OFFLINE
    assert status_color(None) == COLOR_UNKNOWN


def test_build_status_image_size():
    image = build_status_image(True)

    assert image.size == (64, 64)
    assert image.mode == 'RGBA'


def test_build_status_image_for_each_state():
    for state in (True, False, None):
        image = build_status_image(state)
        # 右下角状态点颜色应与状态对应
        assert image.getpixel((56, 56))[:3] == status_color(state)[:3]


def test_tray_degrades_without_pystray(monkeypatch, config_factory):
    monkeypatch.setitem(sys.modules, 'pystray', None)
    controller = AppController(config_factory())
    tray = TrayIcon(controller)

    assert tray.start() is False
    assert tray.available is False
    # 降级后调用 update / stop 也不应报错
    tray.update(online=True)
    tray.stop()


def test_tooltip_text(config_factory):
    controller = AppController(config_factory())
    tray = TrayIcon(controller)

    text = tray.tooltip()
    assert '锐捷' in text
    assert '状态' in text
    assert '守护' in text


def test_message_filter_patch_is_safe():
    """受限环境兼容补丁不应抛异常（返回状态字符串即可）。"""

    from ruijie_eporta.gui.tray import patch_message_filter

    assert patch_message_filter() in ('patched', 'no-attribute', 'unavailable')
