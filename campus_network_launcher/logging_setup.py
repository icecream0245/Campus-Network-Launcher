#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志初始化。

守护模式必须留下可追溯的日志；日志文件默认写在程序目录的 ``logs/`` 下，
写入失败（如只读目录）时自动降级为仅控制台输出。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from . import config

LOG_FORMAT = '%(asctime)s %(levelname)-7s [%(name)s] %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
LOGGER_NAME = 'campus_network_launcher'
DEFAULT_LOG_NAME = 'campus-network-launcher.log'


def default_log_path() -> str:
    return os.path.join(config.program_dir(), 'logs', DEFAULT_LOG_NAME)


def setup_logging(
    *,
    verbose: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 1048576,
    backup_count: int = 3,
) -> logging.Logger:
    """配置并返回主 logger（可重复调用，会先清理旧 handler）。"""

    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover
            pass

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # 有控制台时输出到 stderr（windowed exe 下 sys.stderr 为 None）
    if sys.stderr is not None and getattr(sys.stderr, 'write', None):
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(level)
        console.setFormatter(formatter)
        logger.addHandler(console)

    path = os.path.abspath(log_file) if log_file else default_log_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes if max_bytes and max_bytes > 0 else 0,
            backupCount=backup_count if backup_count and backup_count > 0 else 0,
            encoding='utf-8',
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.debug('日志文件：%s', path)
    except Exception as exc:
        logger.warning('无法写入日志文件 %s：%s（将只输出到控制台）', path, exc)

    if not logger.handlers:  # pragma: no cover - 极端环境
        logger.addHandler(logging.NullHandler())

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取子 logger，统一挂在 ``campus_network_launcher`` 命名空间下。"""

    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + '.'):
        return logging.getLogger(name)
    return logging.getLogger('%s.%s' % (LOGGER_NAME, name))
