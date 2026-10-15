#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from typing import List, Optional

from . import __version__, autostart, config, logging_setup, winutil
from .daemon import EXIT_AUTH_FAILED, EXIT_OK, Daemon
from .notify import Notifier

log = logging.getLogger(__name__)

PROG = 'Ruijie_ePorta_Tool'
DESCRIPTION = '锐捷 ePortal 校园网自动登录工具 v%s（本地迭代版，含可视化界面）' % __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=(
            '不带任何参数运行会打开图形界面并自动开始守护模式；'
            '需要旧版「执行一次就退出」的行为请使用 --once。'
        ),
    )
    parser.add_argument('--config', metavar='PATH', help='指定配置文件路径（默认程序目录下的 config.yml）')
    parser.add_argument('--version', action='version', version='%s %s' % (PROG, __version__))

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--gui', action='store_true', help='打开图形界面（不带参数时的默认行为）')
    mode.add_argument('--watch', '-w', action='store_true', help='守护模式：默认最小化到托盘常驻，掉线自动重连')
    mode.add_argument('--once', action='store_true', help='执行一次：检查校园网 → 已联网询问是否断网 → 未联网则登录')
    mode.add_argument('--login', action='store_true', help='只执行一次登录认证（无界面）')
    mode.add_argument('--logout', action='store_true', help='只执行一次断网（无界面）')
    mode.add_argument('--status', action='store_true', help='输出当前网络状态 JSON（无界面）')
    mode.add_argument('--install-autostart', action='store_true', help='安装开机自启')
    mode.add_argument('--remove-autostart', action='store_true', help='卸载开机自启')

    parser.add_argument('--yes', action='store_true', help='已联网时自动选择断网（不弹窗）')
    parser.add_argument('--no', dest='no_disconnect', action='store_true', help='已联网时不断网（不弹窗）')
    parser.add_argument('--no-watch', action='store_true', help='打开界面时不自动开始守护模式')
    parser.add_argument('--no-tray', action='store_true', help='不创建系统托盘图标（配合 --watch 为纯后台运行）')
    parser.add_argument('--quiet', action='store_true', help='静默模式：不发送系统通知，只写日志')
    parser.add_argument('--no-toast', action='store_true', help='不使用 Windows 通知中心消息')
    parser.add_argument('--dialog', action='store_true', help='额外弹出消息框提示')
    parser.add_argument('--verbose', '-v', action='store_true', help='输出调试日志')
    parser.add_argument('--allow-multiple', action='store_true', help='允许同时运行多个实例')
    parser.add_argument('--log-file', metavar='PATH', help='指定日志文件路径')
    parser.add_argument('--iterations', type=int, metavar='N', help='守护模式下最多循环 N 次后退出（调试用）')
    return parser


def _make_notifier(cfg: dict, args: argparse.Namespace) -> Notifier:
    notify_cfg = cfg.get('notify', {})
    return Notifier(
        toast=bool(notify_cfg.get('toast', True)) and not args.no_toast,
        dialog=bool(notify_cfg.get('dialog', False)) or args.dialog,
        quiet=bool(notify_cfg.get('quiet', False)) or args.quiet,
    )


def _setup_logging(cfg: Optional[dict], args: argparse.Namespace) -> logging.Logger:
    daemon_cfg = (cfg or {}).get('daemon', {})
    return logging_setup.setup_logging(
        verbose=args.verbose,
        log_file=args.log_file or daemon_cfg.get('log_file') or None,
        max_bytes=int(daemon_cfg.get('log_max_bytes', 1048576) or 1048576),
        backup_count=int(daemon_cfg.get('log_backup_count', 3) or 3),
    )


def _report_config_error(exc: Exception, args: argparse.Namespace) -> int:
    logger = _setup_logging(None, args)
    logger.error('%s', str(exc).replace('\n', ' '))
    if not args.quiet:
        winutil.message_box(str(exc), '配置错误', winutil.MB_OK | winutil.MB_ICONERROR)
    return 1


def _handle_autostart(args: argparse.Namespace) -> int:
    logger = _setup_logging(None, args)
    cfg: Optional[dict] = None
    try:
        cfg = config.load_config(args.config, create_if_missing=False)
    except config.ConfigError as exc:
        logger.warning('未能读取配置（将使用默认自启参数）：%s', str(exc).replace('\n', ' '))

    method = autostart.current_method(cfg)
    start_args = autostart.current_args(cfg)

    try:
        if args.install_autostart:
            where = autostart.install(method, args=start_args)
            message = '已安装开机自启\n方式：%s\n位置：%s\n启动参数：%s' % (method, where, start_args)
        else:
            removed = autostart.uninstall(method)
            message = '已移除开机自启（方式：%s，%s）' % (method, '成功' if removed else '未找到自启项')
    except Exception as exc:
        logger.error('设置开机自启失败：%s', exc)
        message = '设置开机自启失败：\n%s' % exc
        if not args.quiet:
            winutil.message_box(message, '开机自启', winutil.MB_OK | winutil.MB_ICONERROR)
        return 1

    logger.info('%s', message.replace('\n', ' '))
    if not args.quiet:
        winutil.message_box(message, '开机自启', winutil.MB_OK | winutil.MB_ICONINFORMATION)
    return EXIT_OK


def _install_signal_handlers(daemon: Daemon) -> None:
    def handler(signum, _frame):  # pragma: no cover - 依赖真实信号
        daemon.request_stop()

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):  # pragma: no cover - 非主线程等
            pass


def _try_gui(
    cfg: dict,
    args: argparse.Namespace,
    logger: logging.Logger,
    *,
    start_watching: bool,
    start_hidden: bool,
) -> Optional[int]:
    """尝试启动图形界面；环境不支持时返回 None 以便回退到命令行模式。"""

    try:
        from .gui.app import gui_available, run_gui
    except Exception as exc:  # pragma: no cover - 缺依赖或非图形平台
        logger.info('图形界面不可用（%s），改用命令行模式', exc)
        return None

    if not gui_available():
        logger.info('当前环境无法创建窗口，改用命令行模式')
        return None

    try:
        return run_gui(
            cfg,
            logger=logger,
            start_watching=start_watching,
            start_hidden=start_hidden,
            enable_tray=not args.no_tray,
        )
    except Exception as exc:  # pragma: no cover - 界面初始化失败时兜底
        logger.warning('图形界面启动失败，改用命令行模式：%s', exc)
        return None


def _print_status(daemon: Daemon, cfg: dict) -> int:
    info = daemon.status()
    info['config_version'] = (cfg.get('main') or {}).get('version')
    method = autostart.current_method(cfg)
    info['autostart'] = {
        'method': method,
        'installed': autostart.is_installed(method),
        'args': autostart.current_args(cfg),
    }
    text = json.dumps(info, ensure_ascii=False, indent=2)
    log.info('状态：%s', text.replace('\n', ' '))
    if sys.stdout is not None:
        print(text)
    return EXIT_OK if info['online'] or info['school_network'] else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.install_autostart or args.remove_autostart:
        return _handle_autostart(args)

    try:
        cfg = config.load_config(args.config)
    except config.ConfigError as exc:
        return _report_config_error(exc, args)

    logger = _setup_logging(cfg, args)
    notifier = _make_notifier(cfg, args)
    logger.info('%s v%s 启动（配置文件：%s）', PROG, __version__, cfg.get('_config_path'))

    if not args.allow_multiple and not args.status:
        if not winutil.acquire_single_instance('ruijie_eporta_tool'):
            logger.warning('检测到已有实例在运行，本次退出')
            notifier.warn(
                '程序已在运行',
                '本程序默认不可多开。\n'
                '如果它正在后台守护，请在系统托盘中打开主界面；\n'
                '确需多开请加 --allow-multiple 参数。',
            )
            return EXIT_OK

    daemon = Daemon(cfg, notifier=notifier, logger=logging.getLogger('ruijie_eporta.daemon'))

    if args.status:
        return _print_status(daemon, cfg)
    if args.login:
        return daemon.login_once()
    if args.logout:
        return daemon.logout_once()

    auto_disconnect: Optional[bool] = None
    if args.yes:
        auto_disconnect = True
    elif args.no_disconnect:
        auto_disconnect = False

    # 守护模式：优先"托盘常驻 + 隐藏窗口"，环境不支持时退化为纯后台
    if args.watch:
        code = _try_gui(cfg, args, logger, start_watching=True, start_hidden=not args.no_tray)
        if code is not None:
            return code
        logger.info('以纯后台方式启动守护模式')
        _install_signal_handlers(daemon)
        return daemon.run(max_iterations=args.iterations)

    # 单次模式（兼容上游行为）
    if args.once:
        return daemon.run_once(auto_disconnect=auto_disconnect)

    # 默认 / --gui：打开界面并自动守护
    code = _try_gui(cfg, args, logger, start_watching=not args.no_watch, start_hidden=False)
    if code is not None:
        return code

    logger.info('图形界面不可用，回退为单次执行模式')
    return daemon.run_once(auto_disconnect=auto_disconnect)


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
