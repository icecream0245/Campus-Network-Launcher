#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面与守护逻辑之间的控制器。

线程模型：

* **主线程**：tkinter 事件循环（只做界面刷新，绝不阻塞）；
* **守护线程**：``Daemon.run()`` 常驻循环，通过 ``stop_event`` 可被立即打断；
* **工作线程**：一次性的登录/断网/状态查询；
* **托盘线程**：pystray 自带消息循环（仅投递请求，不直接碰 tkinter）。

所有跨线程通信都通过 ``queue.Queue`` 完成，界面在 ``poll()`` 中消费。
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .. import autostart
from .. import config as config_module
from ..daemon import (
    EVENT_KEEPALIVE,
    EVENT_LOGIN_FAIL,
    EVENT_LOGIN_OK,
    EVENT_LOGOUT_FAIL,
    EVENT_LOGOUT_OK,
    EVENT_SERVICE_REJECTED,
    EVENT_STATE,
    EVENT_WATCH_STARTED,
    EVENT_WATCH_STOPPED,
    EXIT_OK,
    Daemon,
)
from ..notify import Notifier

log = logging.getLogger(__name__)

# 界面请求（由托盘或其它线程发起，主线程执行）
UI_SHOW = 'show'
UI_QUIT = 'quit'
UI_TOGGLE_WATCH = 'toggle_watch'
UI_LOGIN = 'login'
UI_LOGOUT = 'logout'


@dataclass
class UiEvent:
    """投递给界面的事件。"""

    kind: str
    message: str = ''
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def clock(self) -> str:
        return time.strftime('%H:%M:%S', time.localtime(self.timestamp))


class AppController:
    """把 Daemon 的能力包装成界面友好的异步接口。"""

    def __init__(self, cfg: Dict[str, Any], *, logger: Optional[logging.Logger] = None) -> None:
        self.cfg = cfg
        self.log = logger or log
        self.events: 'queue.Queue[UiEvent]' = queue.Queue()
        self.ui_requests: 'queue.Queue[str]' = queue.Queue()

        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._watch_thread: Optional[threading.Thread] = None
        self._worker: Optional[threading.Thread] = None

        self.watch_started_at: Optional[float] = None
        self.online: Optional[bool] = None
        self.online_detail = ''
        self.last_message = '就绪'
        self.failures = 0
        self._pending_services_done: Optional[Callable[[List[str], str], None]] = None

        notify_cfg = cfg.get('notify', {})
        self.notifier = Notifier(
            toast=bool(notify_cfg.get('toast', True)),
            dialog=False,  # 界面已有提示，不再弹模态框
            quiet=bool(notify_cfg.get('quiet', False)),
            logger=self.log,
        )
        self.daemon = Daemon(cfg, notifier=self.notifier, logger=self.log, listener=self._on_daemon_event)

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def server(self) -> str:
        return str(self.cfg['url']['server'])

    @property
    def user_id(self) -> str:
        return str((self.cfg.get('login_data') or {}).get('userId') or '')

    @property
    def config_path(self) -> str:
        return str(self.cfg.get('_config_path') or '')

    def log_file_path(self) -> str:
        """当前使用的日志文件路径。"""

        configured = (self.cfg.get('daemon') or {}).get('log_file')
        if configured:
            return os.path.abspath(str(configured))
        from ..logging_setup import default_log_path

        return default_log_path()

    @property
    def watching(self) -> bool:
        return self._watch_thread is not None and self._watch_thread.is_alive()

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def watch_uptime(self) -> str:
        if not self.watching or self.watch_started_at is None:
            return '-'
        seconds = int(time.time() - self.watch_started_at)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return '%d 小时 %02d 分' % (hours, minutes)
        if minutes:
            return '%d 分 %02d 秒' % (minutes, secs)
        return '%d 秒' % secs

    def state_text(self) -> str:
        if self.online is None:
            return '未知'
        return '已联网' if self.online else '未联网'

    # ------------------------------------------------------------------ #
    # 事件投递
    # ------------------------------------------------------------------ #
    def _post(self, kind: str, message: str = '', **data: Any) -> None:
        self.events.put(UiEvent(kind, message, data))

    def _on_daemon_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Daemon 回调（运行在守护线程）。"""

        with self._state_lock:
            if event == EVENT_STATE:
                self.online = bool(payload.get('online'))
                self.online_detail = str(payload.get('detail') or '')
                self.last_message = '网络' + ('已连接' if self.online else '已断开')
            elif event == EVENT_LOGIN_OK:
                self.online = True
                self.failures = 0
                self.last_message = '认证成功：%s' % (payload.get('message') or '已联网')
            elif event == EVENT_LOGIN_FAIL:
                self.failures = int(payload.get('failures') or 0)
                self.last_message = '认证失败：%s' % (payload.get('message') or '未知错误')
            elif event == EVENT_LOGOUT_OK:
                self.online = False
                self.last_message = '已断网'
            elif event == EVENT_LOGOUT_FAIL:
                self.last_message = '断网失败：%s' % (payload.get('message') or '未知错误')
            elif event == EVENT_WATCH_STARTED:
                self.last_message = '守护模式已启动'
            elif event == EVENT_WATCH_STOPPED:
                self.watch_started_at = None
                self.last_message = '守护模式已停止'
            elif event == EVENT_KEEPALIVE:
                self.last_message = '保活探测完成'
            elif event == EVENT_SERVICE_REJECTED:
                self.last_message = '疑似服务商不正确：%s' % (payload.get('message') or '')

        # payload 里可能自带 message 字段，需要与 _post 的形参区分开
        data = dict(payload)
        summary = str(data.pop('message', '') or self.last_message)
        self._post(event, summary, **data)

    def request_show(self) -> None:
        self.ui_requests.put(UI_SHOW)

    def request_quit(self) -> None:
        self.ui_requests.put(UI_QUIT)

    def request_toggle_watch(self) -> None:
        self.ui_requests.put(UI_TOGGLE_WATCH)

    def poll_ui_request(self) -> Optional[str]:
        try:
            return self.ui_requests.get_nowait()
        except queue.Empty:
            return None

    def poll_event(self) -> Optional[UiEvent]:
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _run_worker(self, target: Callable[[], None], name: str) -> bool:
        """启动一次性后台任务；已有任务在跑时返回 False。"""

        if self.busy:
            self.log.debug('已有任务在执行，忽略本次 %s 请求', name)
            return False
        self._worker = threading.Thread(target=target, name=name, daemon=True)
        self._worker.start()
        return True

    def login_now(self) -> bool:
        def task() -> None:
            self._post('info', '正在登录认证…')
            code = self.daemon.login_once()
            self._post('done', '登录完成' if code == EXIT_OK else '登录失败')

        return self._run_worker(task, 'eporta-login')

    def logout_now(self) -> bool:
        def task() -> None:
            self._post('info', '正在执行断网…')
            code = self.daemon.logout_once()
            self._post('done', '断网完成' if code == EXIT_OK else '断网失败')

        return self._run_worker(task, 'eporta-logout')

    def refresh_status(self) -> bool:
        def task() -> None:
            info = self.daemon.status()
            with self._state_lock:
                self.online = bool(info.get('online'))
                self.online_detail = str(info.get('online_detail') or '')
                self.last_message = '状态已刷新：%s' % ('已联网' if self.online else '未联网')
            self._post('status', '状态已刷新', **info)

        return self._run_worker(task, 'eporta-status')

    def start_watch(self) -> bool:
        if self.watching:
            return False
        self._stop_event.clear()
        self.daemon = Daemon(
            self.cfg,
            notifier=self.notifier,
            logger=self.log,
            listener=self._on_daemon_event,
            stop_event=self._stop_event,
        )
        self.daemon.failures = self.failures
        self.watch_started_at = time.time()

        def task() -> None:
            try:
                self.daemon.run()
            except Exception as exc:  # pragma: no cover - 兜底
                self.log.exception('守护线程异常退出')
                self._post('error', '守护模式异常退出：%s' % exc)
            finally:
                self.watch_started_at = None
                self._post('watch_stopped', '守护模式已停止')

        self._watch_thread = threading.Thread(target=task, name='eporta-watch', daemon=True)
        self._watch_thread.start()
        self.last_message = '守护模式已启动'
        self._post('watch_started', '守护模式已启动')
        return True

    def stop_watch(self) -> bool:
        if not self.watching:
            return False
        self.daemon.request_stop()
        return True

    def toggle_watch(self) -> None:
        if self.watching:
            self.stop_watch()
        else:
            self.start_watch()

    # ------------------------------------------------------------------ #
    # 首次设置向导
    # ------------------------------------------------------------------ #
    @property
    def needs_profile(self) -> bool:
        """配置里是否还没有账号信息（需要弹首次设置向导）。"""

        return bool(self.cfg.get('_needs_profile'))

    def current_service(self) -> str:
        return str((self.cfg.get('auto') or {}).get('service') or '').strip()

    def service_options(self) -> List[str]:
        """可选服务商列表：优先用配置里缓存的，否则用内置兜底。"""

        auto = self.cfg.get('auto') or {}
        options = [str(item).strip() for item in (auto.get('service_options') or []) if str(item or '').strip()]
        if options:
            return options
        from .dialogs import FALLBACK_SERVICES

        return list(FALLBACK_SERVICES)

    def _remember_service_options(self, services: List[str]) -> None:
        auto = self.cfg.setdefault('auto', {})
        cleaned = [str(item).strip() for item in (services or []) if str(item or '').strip()]
        if cleaned:
            auto['service_options'] = cleaned

    def fetch_services_async(self, done: Callable[[List[str], str], None]) -> bool:
        """后台抓取服务商列表；完成后在主线程调用 ``done(services, error)``。

        实现方式：工作线程把结果投递成事件，界面在 _pump 里通过 ``poll_service_result`` 取走，
        这里额外记录 ``done`` 回调，由界面的 _handle_event 消费。
        """

        def task() -> None:
            from ..portal import fetch_service_options

            try:
                services, error = fetch_service_options(self.cfg, logger=self.log)
            except Exception as exc:  # pragma: no cover - 兜底
                services, error = [], '%s: %s' % (type(exc).__name__, exc)
            if services:
                self._remember_service_options(services)
            with self._state_lock:
                self._pending_services_done = done
            self._post(
                'services',
                ('已获取 %d 个服务商' % len(services)) if services else ('获取服务商失败：%s' % error),
                services=list(services),
                error=error,
            )

        return self._run_worker(task, 'eporta-services')

    def take_services_callback(self) -> Optional[Callable[[List[str], str], None]]:
        """取出并清空等待中的服务商列表回调（界面收到 services 事件时调用）。"""

        with self._state_lock:
            callback = self._pending_services_done
            self._pending_services_done = None
        return callback

    def save_profile(self, user_id: str, password: str, service: str, options: List[str]) -> str:
        """保存首次设置填写的账号信息（含服务商），返回给用户看的提示。"""

        from .. import config as config_module

        fields = config_module.build_profile_fields(user_id, password, service, options)
        try:
            _path, backup = config_module.update_auto_fields(self.config_path, fields)
        except config_module.ConfigError as exc:
            self.log.error('保存账号信息失败：%s', exc)
            return '保存失败：%s' % exc

        self.cfg.setdefault('auto', {}).update(fields)
        login_data = self.cfg.get('login_data')
        if isinstance(login_data, dict):
            login_data['userId'] = fields['user_id']
        self.cfg['_needs_profile'] = False
        self.log.info('已保存账号信息（服务商：%s）', service or '默认')
        suffix = '，原配置已备份' if backup else ''
        return '已保存账号信息（服务商：%s）%s' % (service or '默认', suffix)

    def apply_service(self, service: str) -> str:
        """把用户新选的服务商写入配置，并在守护中立即重试。"""

        from .. import config as config_module

        try:
            _path, _backup = config_module.update_auto_fields(self.config_path, {'service': service})
        except config_module.ConfigError as exc:
            self.log.error('保存服务商失败：%s', exc)
            return '保存服务商失败：%s' % exc

        self.cfg.setdefault('auto', {})['service'] = str(service or '').strip()
        was_watching = self.watching
        if was_watching:
            self.stop_watch()
        self.daemon.update_service(service)
        if was_watching:
            self.start_watch()
        self.log.info('服务商已切换为「%s」%s', service, '，正在重新连接' if was_watching else '')
        return '服务商已改为「%s」%s' % (service, '，正在重新连接…' if was_watching else '，将在下次登录时生效')

    def reload_config(self) -> str:
        """重新加载配置文件并重建守护（首次设置保存后调用）。"""

        from .. import config as config_module

        was_watching = self.watching
        if was_watching:
            self.stop_watch()
        try:
            new_cfg = config_module.load_config(self.config_path, allow_incomplete=True)
        except config_module.ConfigError as exc:
            self.log.error('重新加载配置失败：%s', exc)
            return '重新加载配置失败：%s' % exc

        self.cfg = new_cfg
        self.daemon = Daemon(new_cfg, notifier=self.notifier, logger=self.log, listener=self._on_daemon_event)
        if was_watching:
            self.start_watch()
        self.log.info('配置已重新加载（需要首次设置：%s）', new_cfg.get('_needs_profile'))
        return '配置已重新加载'

    # ------------------------------------------------------------------ #
    # 自动配置（首次运行自动获取参数并写回 config.yml）
    # ------------------------------------------------------------------ #
    def needs_auto_setup(self) -> bool:
        """是否需要首次自动获取参数：启用了免抓包、有密码、但还没有抓包参数。"""

        auto = self.cfg.get('auto') or {}
        if not bool(auto.get('enabled')):
            return False
        if not str(auto.get('password') or '').strip():
            return False
        if not str(auto.get('user_id') or (self.cfg.get('login_data') or {}).get('userId') or '').strip():
            return False
        return not str((self.cfg.get('login_data') or {}).get('queryString') or '').strip()

    def auto_setup(self) -> bool:
        """在后台执行自动配置，成功后会覆盖写入 config.yml（带备份）。"""

        def task() -> None:
            from .. import config as config_module
            from ..portal import PortalDiscovery

            auto = self.cfg.get('auto') or {}
            login_data = self.cfg.get('login_data') or {}
            user_id = str(auto.get('user_id') or login_data.get('userId') or '')
            password = str(auto.get('password') or '')
            service = str(auto.get('service') or '')
            self._post('info', '正在访问学校认证页，自动获取参数…')
            try:
                discovery = PortalDiscovery(
                    probes=self.cfg['network']['probes'],
                    timeout=self.cfg['network']['timeout'],
                    logger=self.log,
                )
                discovered = discovery.setup_config(user_id, password, service)
                new_cfg = config_module.build_config_from_discovery(discovered, auto_cfg=auto)
                path, backup = config_module.save_config(
                    self.config_path,
                    new_cfg,
                    header=config_module.discovered_header(discovered),
                )
            except Exception as exc:
                self.log.warning('自动配置失败：%s', exc)
                self._post('error', '自动获取参数失败：%s' % exc)
                return

            details = discovered.get('details') or {}
            message = '已自动获取参数并写入配置（服务器 %s，运营商 %s，备份 %s）' % (
                (discovered.get('url') or {}).get('server', '-'),
                details.get('service_used') or '-',
                backup or '无',
            )
            self.log.info('%s', message)
            self._post('auto_setup_ok', message, path=path, backup=backup)

        return self._run_worker(task, 'eporta-auto-setup')

    # ------------------------------------------------------------------ #
    # 开机自启
    # ------------------------------------------------------------------ #
    def autostart_method(self) -> str:
        return autostart.current_method(self.cfg)

    def autostart_enabled(self) -> bool:
        try:
            return autostart.is_installed(self.autostart_method())
        except Exception as exc:  # pragma: no cover
            self.log.debug('查询开机自启状态失败：%s', exc)
            return False

    def set_autostart(self, enabled: bool) -> str:
        """开启/关闭开机自启，返回给用户看的提示文本。"""

        method = self.autostart_method()
        try:
            if enabled:
                where = autostart.install(method, args=autostart.current_args(self.cfg))
                return '已设置开机自启（%s）\n%s' % (method, where)
            removed = autostart.uninstall(method)
            return '已取消开机自启（%s）' % ('成功' if removed else '未找到自启项')
        except Exception as exc:
            self.log.error('设置开机自启失败：%s', exc)
            return '设置开机自启失败：%s' % exc

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def shutdown(self, timeout: float = 5.0) -> None:
        """停止守护线程并等待退出。"""

        self.stop_watch()
        thread = self._watch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._watch_thread = None


def summarize_probes(probes: List[Any]) -> str:
    """把探测配置整理成一行可读文本。"""

    parts = []
    for item in probes:
        if isinstance(item, dict):
            parts.append(str(item.get('url') or ''))
        else:
            parts.append(str(item))
    return ' / '.join(part for part in parts if part)
