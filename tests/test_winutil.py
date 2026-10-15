#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""winutil 测试：打开文件的回退链（用 monkeypatch，不会真的启动程序）。

背景：很多机器上 `.yml` / `.yaml` 没有文件关联，`os.startfile` 会直接失败，
因此需要「默认程序 → 记事本 → 资源管理器定位」的逐级回退。
"""

from __future__ import annotations

import pytest

from ruijie_eporta import winutil


@pytest.fixture
def yml_file(tmp_path):
    path = tmp_path / 'config.yml'
    path.write_text('main:\n  version: 3\n', encoding='utf-8')
    return str(path)


@pytest.fixture
def bin_file(tmp_path):
    path = tmp_path / 'data.bin'
    path.write_bytes(b'\x00\x01')
    return str(path)


def test_open_path_empty_returns_empty():
    assert winutil.open_path('') == ''


def test_open_path_uses_default_program(monkeypatch, yml_file):
    calls = []
    monkeypatch.setattr(winutil, 'has_file_association', lambda path: True)
    monkeypatch.setattr(winutil.os, 'startfile', lambda path: calls.append(path), raising=False)

    assert winutil.open_path(yml_file) == 'default'
    assert calls == [yml_file]


def test_open_path_prefers_notepad_when_requested(monkeypatch, yml_file):
    default_calls = []
    started = []
    monkeypatch.setattr(winutil.os, 'startfile', lambda path: default_calls.append(path), raising=False)
    monkeypatch.setattr(winutil.subprocess, 'Popen', lambda args, **kwargs: started.append(args))

    assert winutil.open_path(yml_file, prefer_text_editor=True) == 'notepad'
    assert default_calls == []
    assert started and started[0][0].lower().startswith('notepad')


def test_open_path_falls_back_to_notepad(monkeypatch, yml_file):
    def no_association(_path):
        raise OSError('没有应用程序与此操作的指定文件有关联')

    started = []
    monkeypatch.setattr(winutil.os, 'startfile', no_association, raising=False)
    monkeypatch.setattr(winutil.subprocess, 'Popen', lambda args, **kwargs: started.append(args))

    assert winutil.open_path(yml_file) == 'notepad'
    assert started and started[0][0].lower().startswith('notepad')
    assert started[0][1] == yml_file


def test_open_path_falls_back_to_explorer_for_binary(monkeypatch, bin_file):
    def no_association(_path):
        raise OSError('没有关联')

    started = []
    monkeypatch.setattr(winutil.os, 'startfile', no_association, raising=False)
    monkeypatch.setattr(winutil.subprocess, 'Popen', lambda args, **kwargs: started.append(args))

    # 非文本后缀不会用记事本兜底，改为在资源管理器中定位
    assert winutil.open_path(bin_file) == 'explorer'
    assert started and started[0][0].lower().startswith('explorer')
    assert started[0][1].startswith('/select,')


def test_open_path_missing_file_reveals_parent(monkeypatch, tmp_path):
    missing = str(tmp_path / 'not-here.yml')
    started = []
    monkeypatch.setattr(winutil.subprocess, 'Popen', lambda args, **kwargs: started.append(args))

    assert winutil.open_path(missing) == 'explorer'
    assert started and started[0][1].startswith('/select,' + str(tmp_path))


def test_open_path_returns_empty_when_everything_fails(monkeypatch, yml_file):
    def boom(*_args, **_kwargs):
        raise OSError('全部失败')

    monkeypatch.setattr(winutil.os, 'startfile', boom, raising=False)
    monkeypatch.setattr(winutil.subprocess, 'Popen', boom)

    assert winutil.open_path(yml_file) == ''


def test_text_editor_extensions_cover_config_formats():
    for ext in ('.yml', '.yaml', '.log', '.txt', '.json'):
        assert ext in winutil.TEXT_EDITOR_EXTENSIONS


def test_has_file_association_returns_bool(tmp_path):
    target = tmp_path / 'x.unknownext'
    target.write_text('x', encoding='utf-8')

    assert isinstance(winutil.has_file_association(str(target)), bool)


def test_open_path_skips_default_program_without_association(monkeypatch, yml_file):
    """没有文件关联时必须跳过 os.startfile（它会静默失败），直接用记事本。"""

    calls = []
    monkeypatch.setattr(winutil, 'has_file_association', lambda path: False)
    monkeypatch.setattr(winutil.os, 'startfile', lambda path: calls.append(('startfile', path)), raising=False)
    monkeypatch.setattr(winutil.subprocess, 'Popen', lambda args, **kwargs: calls.append(('popen', args)))

    assert winutil.open_path(yml_file) == 'notepad'
    # 只应调用记事本，不应调用 startfile
    assert [kind for kind, _ in calls] == ['popen']


def test_open_path_uses_default_when_association_exists(monkeypatch, yml_file):
    calls = []
    monkeypatch.setattr(winutil, 'has_file_association', lambda path: True)
    monkeypatch.setattr(winutil.os, 'startfile', lambda path: calls.append(path), raising=False)

    assert winutil.open_path(yml_file) == 'default'
    assert calls == [yml_file]


def test_has_file_association_false_without_registry_entries(monkeypatch):
    """注册表里查不到任何默认程序时应判定为无关联。"""

    import winreg

    def fake_open_key(_root, subkey, *args, **kwargs):
        raise FileNotFoundError(subkey)

    monkeypatch.setattr(winreg, 'OpenKey', fake_open_key)
    assert winutil.has_file_association('x.yml') is False


def test_has_file_association_true_for_appx_user_choice(monkeypatch):
    """UWP 应用（AppX 开头的 ProgID）没有 shell\\open\\command，但同样算有关联。"""

    import winreg

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_open_key(_root, subkey, *args, **kwargs):
        if 'UserChoice' in subkey:
            return FakeKey()
        raise FileNotFoundError(subkey)

    monkeypatch.setattr(winreg, 'OpenKey', fake_open_key)
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda _key, _name: ('AppX4ztfk9wxr86nxmzzq47px0nh0e58b8fw', 1))

    assert winutil.has_file_association('x.log') is True
