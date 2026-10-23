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

PROG = 'Campus Network Launcher'
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
    mode.add_argument('--auto-setup', action='store_true', help='自动获取认证参数并写入配置文件（免抓包）')
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
    parser.add_argument('--bootstrap', action='store_true', help=argparse.SUPPRESS)
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
    bootstrap_on_start: bool = False,
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
            bootstrap_on_start=bootstrap_on_start,
            enable_tray=not args.no_tray,
        )
    except Exception as exc:  # pragma: no cover - 界面初始化失败时兜底
        logger.warning('图形界面启动失败，改用命令行模式：%s', exc)
        return None


def _run_auto_setup(cfg: dict, logger: logging.Logger, args: argparse.Namespace) -> int:
    """执行一次自动配置：探测认证参数并写回 config.yml。"""

    from .portal import PortalDiscovery

    auto = cfg.get('auto') or {}
    login_data = cfg.get('login_data') or {}
    user_id = str(auto.get('user_id') or login_data.get('userId') or '')
    password = str(auto.get('password') or '')
    service = str(auto.get('service') or '')

    if not user_id or not password:
        message = '自动配置需要先在 config.yml 的 auto 段填写 user_id（学号）与 password（密码）。'
        logger.error(message)
        if not args.quiet:
            winutil.message_box(message, '自动配置', winutil.MB_OK | winutil.MB_ICONWARNING)
        return 1

    discovery = PortalDiscovery(
        probes=cfg['network']['probes'],
        timeout=cfg['network']['timeout'],
        logger=logger,
    )
    try:
        from .portal import _portal_url_from_config

        discovered = discovery.setup_config(
            user_id, password, service, portal_url=_portal_url_from_config(cfg)
        )
    except Exception as exc:
        message = '自动获取认证参数失败：%s' % exc
        logger.error(message)
        if not args.quiet:
            winutil.message_box(message, '自动配置', winutil.MB_OK | winutil.MB_ICONERROR)
        return 1

    new_cfg = config.build_config_from_discovery(discovered, auto_cfg=auto)
    try:
        path, backup = config.save_config(
            cfg.get('_config_path') or config.default_config_path(),
            new_cfg,
            header=config.discovered_header(discovered),
        )
    except config.ConfigError as exc:
        logger.error('%s', exc)
        if not args.quiet:
            winutil.message_box(str(exc), '自动配置', winutil.MB_OK | winutil.MB_ICONERROR)
        return 1

    details = discovered.get('details') or {}
    message = (
        '已自动获取认证参数并写入配置\n'
        '文件：%s\n备份：%s\n'
        '服务器：%s\n运营商：%s（密码加密：%s）'
        % (
            path,
            backup or '(无)',
            (discovered.get('url') or {}).get('server', '-'),
            details.get('service_used') or '-',
            '是' if details.get('password_encrypted') else '否',
        )
    )
    logger.info('%s', message.replace('\n', ' '))
    if not args.quiet:
        winutil.message_box(message, '自动配置完成', winutil.MB_OK | winutil.MB_ICONINFORMATION)
    return EXIT_OK


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
    except config.ProfileIncompleteError as exc:
        # 首次运行：命令行模式仍然报错退出，图形界面则放宽校验后弹向导
        if args.status or args.login or args.logout or args.auto_setup:
            return _report_config_error(exc, args)
        try:
            cfg = config.load_config(args.config, allow_incomplete=True)
        except config.ConfigError as retry_exc:
            return _report_config_error(retry_exc, args)
    except config.ConfigError as exc:
        return _report_config_error(exc, args)

    logger = _setup_logging(cfg, args)
    notifier = _make_notifier(cfg, args)
    try:
        config.migrate_plain_password(cfg.get('_config_path') or args.config or config.default_config_path())
    except Exception as exc:  # pragma: no cover - 迁移失败不影响启动
        logger.warning('迁移明文密码失败：%s', exc)
    logger.info('%s v%s 启动（配置文件：%s）', PROG, __version__, cfg.get('_config_path'))

    if not args.allow_multiple and not args.status and not args.bootstrap:
        if not winutil.acquire_single_instance('campus_network_launcher_tool'):
            logger.warning('检测到已有实例在运行，本次退出')
            notifier.warn(
                '程序已在运行',
                '本程序默认不可多开。\n'
                '如果它正在后台守护，请在系统托盘中打开主界面；\n'
                '确需多开请加 --allow-multiple 参数。',
            )
            return EXIT_OK

    daemon = Daemon(cfg, notifier=notifier, logger=logging.getLogger('campus_network_launcher.daemon'))

    if args.auto_setup:
        return _run_auto_setup(cfg, logger, args)

    # 保存配置后重启进入的自动执行模式
    if args.bootstrap:
        code = _try_gui(cfg, args, logger, start_watching=True, start_hidden=False, bootstrap_on_start=True)
        if code is not None:
            return code
        logger.info('图形界面不可用，--bootstrap 回退为命令行自动获取参数')
        return _run_auto_setup(cfg, logger, args)
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
        # 首次运行时必须让用户看到向导窗口
        start_hidden = (not args.no_tray) and not cfg.get('_needs_profile')
        code = _try_gui(cfg, args, logger, start_watching=True, start_hidden=start_hidden)
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
