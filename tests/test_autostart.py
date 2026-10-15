#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自启模块测试（只验证路径与命令拼接，不真的写入系统）。"""

from __future__ import annotations

import os

from ruijie_eporta import autostart, config


def test_launch_target_in_dev_mode():
    target, arguments, workdir = autostart.launch_target('--watch --quiet')

    assert os.path.basename(target).lower() in ('pythonw.exe', 'python.exe')
    assert arguments == '-m ruijie_eporta --watch --quiet'
    assert workdir == config.program_dir()


def test_launch_target_without_args():
    _target, arguments, _workdir = autostart.launch_target('')
    assert arguments == '-m ruijie_eporta'


def test_ps_quote_escapes_single_quotes():
    assert autostart._ps_quote("C:\\a'b") == "'C:\\a''b'"  # noqa: SLF001


def test_startup_paths():
    startup = autostart.startup_dir()
    assert startup.endswith(os.path.join('Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup'))
    assert autostart.shortcut_path().endswith('.lnk')
    assert os.path.dirname(autostart.shortcut_path()) == startup


def test_is_installed_returns_bool():
    assert isinstance(autostart.is_installed('startup'), bool)
    assert isinstance(autostart.is_installed('registry'), bool)


def test_registry_helpers_use_run_key():
    assert autostart.RUN_KEY.endswith('CurrentVersion\\Run')


def test_current_method_and_args_from_config(config_factory):
    cfg = config_factory(extra='autostart:\n  method: registry\n  args: --watch --yes\n')

    assert autostart.current_method(cfg) == 'registry'
    assert autostart.current_args(cfg) == '--watch --yes'
    # 非法值回退到默认
    assert autostart.current_method({'autostart': {'method': 'weird'}}) == 'startup'


def test_default_args_include_watch():
    assert '--watch' in autostart.DEFAULT_ARGS


def test_describe_text_mentions_state():
    text = autostart.describe('startup')
    assert '已启用' in text or '未启用' in text
    assert 'startup' in text
