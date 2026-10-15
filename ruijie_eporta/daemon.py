#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守护循环：掉线自动重连 + 保活 + 失败指数退避。

设计要点（针对真实校园网环境）：

* **不在离线时盲目重试**：先确认认证服务器可达，避免无意义的请求；
* **指数退避**：登录失败后等待时间翻倍递增，默认上限 5 分钟，
  避免因参数错误被学校认证系统临时封禁（上游 README 提到过 3-5 分钟封禁）；
* **可注入时钟与 sleep**：便于单元测试在不联网的情况下验证状态机；
* **单次模式保留上游行为**：已联网时询问是否断网。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from . import network, winutil
from .eportal import AuthResult, EportalClient
from .notify import Notifier

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_AUTH_FAILED = 2

_MAX_BACKOFF_EXPONENT = 20


class Daemon:
    """认证守护进程。"""

    def __init__(
        self,
        cfg: Dict[str, Any],
        client: Optional[EportalClient] = None,
        *,
        notifier: Optional[Notifier] = None,
        logger: Optional[logging.Logger] = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        online_probe: Optional[Callable[[], network.ProbeResult]] = None,
        reach_probe: Optional[Callable[[], bool]] = None,
        ask_disconnect: Optional[Callable[[], bool]] = None,
    ) -> None:
        daemon_cfg = cfg.get('daemon', {})
        self.cfg = cfg
        self.log = logger or log
        self._client = client or EportalClient(cfg, logger=self.log)
        self._notifier = notifier or Notifier(
            toast=cfg.get('notify', {}).get('toast', True),
            dialog=cfg.get('notify', {}).get('dialog', False),
            quiet=cfg.get('notify', {}).get('quiet', False),
            logger=self.log,
        )
        self._sleep = sleep
        self._now = now
        self._stop = False

        self.server = str(cfg['url']['server'])
        self.probes = list(cfg.get('network', {}).get('probes') or network.DEFAULT_PROBES)
        self.probe_timeout = float(cfg.get('network', {}).get('timeout', 3) or 3)

        self.check_school_network = bool(cfg.get('funtion', {}).get('check_school_network', True))
        self.disconnect_network = bool(cfg.get('funtion', {}).get('disconnect_network', True))

        self.check_interval = float(daemon_cfg.get('check_interval', 30) or 30)
        self.keepalive_interval = float(daemon_cfg.get('keepalive_interval', 0) or 0)
        self.retry_interval = float(daemon_cfg.get('retry_interval', 5) or 5)
        self.max_retry_interval = float(daemon_cfg.get('max_retry_interval', 300) or 300)
        self.cooldown_after_failures = int(daemon_cfg.get('cooldown_after_failures', 0) or 0)
        self.cooldown = float(daemon_cfg.get('cooldown', 180) or 0)

        self._online_probe = online_probe or self._default_online_probe
        self._reach_probe = reach_probe or self._default_reach_probe
        self._ask_disconnect = ask_disconnect or self._default_ask_disconnect

        self.failures = 0
        self._last_keepalive = self._now()

    # ------------------------------------------------------------------ #
    # 默认探测实现
    # ------------------------------------------------------------------ #
    def _default_online_probe(self) -> network.ProbeResult:
        return network.is_online(self.probes, self.probe_timeout)

    def _default_reach_probe(self) -> bool:
        return network.can_reach(self.server, self.probe_timeout)

    def _default_ask_disconnect(self) -> bool:
        return winutil.ask_yes_no('设备目前已联网，是否需要断网？', '网络已连接')

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        """请求退出守护循环（供信号处理调用）。"""

        self._stop = True
        self.log.info('收到退出信号，将在当前等待结束后停止…')

    @property
    def stopping(self) -> bool:
        return self._stop

    # ------------------------------------------------------------------ #
    # 单次动作
    # ------------------------------------------------------------------ #
    def login_once(self) -> int:
        """执行一次登录认证。"""

        self.log.info('开始登录认证：%s', self.server)
        result = self._client.login()
        if result.ok:
            self.failures = 0
            self.log.info('认证成功：%s', result.message or '已联网')
            self._notifier.info('联网成功', result.message or '网络已连接！')
            return EXIT_OK
        message = result.error or result.message or '未知错误'
        self.log.error('认证失败：%s', message)
        self._notifier.error('联网失败', message)
        return EXIT_AUTH_FAILED

    def logout_once(self) -> int:
        """执行一次断网（下线）。"""

        self.log.info('开始断网请求：%s', self.server)
        result = self._client.logout()
        if result.ok:
            self.log.info('断网成功：%s', result.message or '已断网')
            self._notifier.info('已断网', result.message or '断网成功！')
            return EXIT_OK
        message = result.error or result.message or '未知错误'
        self.log.error('断网失败：%s', message)
        self._notifier.error('断网失败', message)
        return EXIT_AUTH_FAILED

    def status(self) -> Dict[str, Any]:
        """收集当前状态信息（供 --status 使用）。"""

        probe = self._online_probe()
        reachable = self._reach_probe()
        return {
            'config_path': self.cfg.get('_config_path'),
            'server': self.server,
            'user_id': (self.cfg.get('login_data') or {}).get('userId'),
            'online': probe.online,
            'online_detail': probe.detail,
            'school_network': reachable,
            'probes': self.probes,
        }

    # ------------------------------------------------------------------ #
    # 单次模式（保持上游行为）
    # ------------------------------------------------------------------ #
    def run_once(self, *, auto_disconnect: Optional[bool] = None) -> int:
        """兼容旧版的一次性执行流程。

        Args:
            auto_disconnect: True/False 直接决定联网时是否断网；
                None 表示弹出对话框询问用户（旧版行为）。
        """

        if self.check_school_network and not self._reach_probe():
            self._notifier.warn(
                '非校园网环境',
                '当前不在校园网环境，10 秒后重新检测\n'
                '若系统刚启动可能还没连上网络，属正常现象',
            )
            self._sleep(10)
            if not self._reach_probe():
                self._notifier.info('非校园网环境', '当前不在校园网环境，程序退出')
                return EXIT_OK

        probe = self._online_probe()
        if probe.online:
            if self.disconnect_network:
                if auto_disconnect is None:
                    auto_disconnect = bool(self._ask_disconnect())
                if auto_disconnect:
                    return self.logout_once()
                self.log.info('用户选择不断网，程序退出')
                return EXIT_OK
            self._notifier.info('设备已联网', '网络本来就是通的噢~')
            return EXIT_OK

        self.log.info('检测到未联网（%s），尝试认证', probe.detail)
        return self.login_once()

    # ------------------------------------------------------------------ #
    # 守护模式
    # ------------------------------------------------------------------ #
    def run(self, max_iterations: Optional[int] = None) -> int:
        """常驻循环：网络掉线自动重连，在线时按需保活。"""

        self.log.info(
            '守护模式启动：检测间隔 %.0fs / 重连间隔 %.0fs（上限 %.0fs）/ 保活 %s',
            self.check_interval,
            self.retry_interval,
            self.max_retry_interval,
            '%.0fs' % self.keepalive_interval if self.keepalive_interval > 0 else '关闭',
        )

        iterations = 0
        while not self._stop:
            iterations += 1
            if max_iterations is not None and iterations > max_iterations:
                self.log.debug('已达到最大循环次数 %s，退出守护循环', max_iterations)
                break

            probe = self._online_probe()
            if probe.online:
                if self.failures:
                    self.log.info('网络已恢复（%s）', probe.detail)
                    self._notifier.info('网络已恢复', '设备已重新联网')
                    self.failures = 0
                self._maybe_keepalive()
                self._sleep(self.check_interval)
                continue

            if self._stop:
                break

            if self.check_school_network and not self._reach_probe():
                self.log.info(
                    '未检测到校园网认证服务器（%s），%.0fs 后重新检测（不发起认证）',
                    self.server,
                    self.check_interval,
                )
                self._sleep(self.check_interval)
                continue

            self._reconnect_once()

        self.log.info('守护模式已退出')
        return EXIT_OK

    def _reconnect_once(self) -> AuthResult:
        """掉线后的一次重连尝试，内部完成退避等待。"""

        self.log.info('检测到未联网，开始第 %d 次重连尝试…', self.failures + 1)
        result = self._client.login()

        if result.ok:
            self.failures = 0
            self.log.info('认证成功：%s', result.message or '已联网')
            self._notifier.info('联网成功', result.message or '网络已连接！')
            self._sleep(self.check_interval)
            return result

        self.failures += 1
        delay = self.backoff_delay(self.failures)
        message = result.error or result.message or '未知错误'
        self.log.warning('第 %d 次重连失败：%s（%.0fs 后重试）', self.failures, message, delay)
        if self.failures == 1 or self.failures % 5 == 0:
            self._notifier.warn(
                '联网失败',
                '%s\n已连续失败 %d 次，程序会继续尝试（等待 %.0f 秒）' % (message, self.failures, delay),
            )
        self._sleep(delay)
        return result

    def backoff_delay(self, failures: int) -> float:
        """根据连续失败次数计算下一次重试等待时间。"""

        exponent = min(max(failures - 1, 0), _MAX_BACKOFF_EXPONENT)
        delay = self.retry_interval * (2**exponent)
        delay = min(delay, self.max_retry_interval)
        if self.cooldown_after_failures and failures >= self.cooldown_after_failures:
            delay = max(delay, self.cooldown)
        return delay

    def _maybe_keepalive(self) -> None:
        if self.keepalive_interval <= 0:
            return
        if self._now() - self._last_keepalive < self.keepalive_interval:
            return
        self.log.debug('发送保活探测，维持链路活跃')
        self._online_probe()
        self._last_keepalive = self._now()
