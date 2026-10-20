#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可视化界面：tkinter 主窗口 + 系统托盘。"""

from __future__ import annotations

__all__ = ['run_gui']


def run_gui(cfg, **kwargs):  # pragma: no cover - 延迟导入，避免无界面环境加载 tkinter
    from .app import run_gui as _run_gui

    return _run_gui(cfg, **kwargs)
