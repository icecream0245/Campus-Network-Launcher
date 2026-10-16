#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置读取、合并与校验。

设计原则：**与上游 v3 配置格式完全兼容**。老配置文件（含 ``funtion`` 拼写、
``login_data`` 缺省项等）无需任何改动即可直接使用；本迭代版新增的
``daemon`` / ``network`` / ``notify`` / ``autostart`` 段落全部是可选的，
缺失时使用内置默认值。
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import yaml

CONFIG_VERSION = 3
CONFIG_FILENAME = 'config.yml'
PLACEHOLDER_USER_ID = '00000000000'
ENV_CONFIG_PATH = 'RUJIE_EPORTA_CONFIG'

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置读取或校验失败。"""


DEFAULT_CONFIG_TEMPLATE = '''\
# 本配置文件内容需要根据学校服务器设置动态调整
main:
  version: 3 # 配置文件版本号，请勿更改

funtion:
  check_school_network: true # 是否检查校园网环境
  disconnect_network: true # 是否开启断网功能

url:
  server: http://127.0.0.1 # 校园网登录服务器的地址，用于判断当前网络环境是否为校园网环境
  login: /eportal/InterFace.do?method=login # 校园网登录地址，无需服务器地址
  logout: /eportal/InterFace.do?method=logout # 校园网断线地址，无需服务器地址

# 请根据抓包结果调整条目与内容
cookie: ''

# 请根据抓包结果调整条目与内容（?key1=value1&key2=value2）
# 请不要在密码栏填入明文密码
login_data:
  userId: '00000000000'
  password:
  service:
  queryString:
  operatorPwd:
  operatorUserId:
  validcode:
  passwordEncrypt: true

# 一般来说不填参数也能用，但请不要删除（?key1=value1&key2=value2）
logout_data:

headers:
  Referer:

# ==================== 以下为本地迭代版新增配置（全部可选） ====================
# 缺失或删除时使用内置默认值，旧的 v3 配置文件可以直接沿用。

# 守护模式（--watch）：掉线自动重连 + 保活
daemon:
  check_interval: 30 # 网络正常时每隔多少秒检测一次
  keepalive_interval: 0 # >0 时：网络正常也每隔这么多秒发一次保活探测（0 = 关闭）
  retry_interval: 5 # 掉线后首次重连等待秒数
  max_retry_interval: 300 # 重连等待的指数退避上限（秒），避免频繁重试被学校封禁
  cooldown_after_failures: 0 # 连续失败达到该次数后额外冷却（0 = 关闭）
  cooldown: 180 # 触发冷却时至少等待的秒数
  login_timeout: 10 # 认证请求超时（秒）
  probe_timeout: 3 # 网络探测超时（秒）
  log_file: '' # 日志文件路径，留空 = 程序目录下 logs/ruijie-eporta.log
  log_max_bytes: 1048576 # 单个日志文件大小上限（字节）
  log_backup_count: 3 # 保留的历史日志份数

# 联网状态探测（按顺序尝试，任一成功即视为已联网）
network:
  probes:
    - http://connect.rom.miui.com/generate_204
    # 也可以用映射形式做内容校验，进一步排除被网关劫持后返回的假页面
    - url: http://www.msftconnecttest.com/connecttest.txt
      contains: Microsoft Connect Test
  timeout: 3

# 通知方式
notify:
  toast: true # Windows 通知中心消息
  dialog: false # 弹窗（适合手动双击运行；守护模式下建议保持 false）
  quiet: false # 静默：只在出错时通知

# 开机自启（--install-autostart / --remove-autostart）
autostart:
  method: startup # startup = 启动目录快捷方式；registry = 注册表 Run 项
  args: --watch --quiet # 自启时附加的命令行参数

# ==================== 免抓包模式（可选，强烈推荐） ====================
# 打开后只需要填学号与密码：程序会自行访问认证页，自动获取
# wlanuserip / mac / 公钥 等参数，无需再用浏览器抓包。
# 启用后上面的 url / cookie / login_data.queryString / headers.Referer 都可以留空。
auto:
  enabled: false # 改成 true 即启用免抓包模式
  user_id: '' # 你的学号（留空则使用 login_data.userId）
  password: '' # 你的密码（明文，仅本机保存，请注意不要泄露本文件）
  service: '' # 运营商/服务名，留空则自动使用认证页里的第一项
'''

DEFAULT_OPTIONS: Dict[str, Any] = {
    'daemon': {
        'check_interval': 30,
        'keepalive_interval': 0,
        'retry_interval': 5,
        'max_retry_interval': 300,
        'cooldown_after_failures': 0,
        'cooldown': 180,
        'login_timeout': 10,
        'probe_timeout': 3,
        'log_file': '',
        'log_max_bytes': 1048576,
        'log_backup_count': 3,
    },
    'network': {
        'probes': [
            'http://connect.rom.miui.com/generate_204',
            {
                'url': 'http://www.msftconnecttest.com/connecttest.txt',
                'contains': 'Microsoft Connect Test',
            },
        ],
        'timeout': 3,
    },
    'notify': {
        'toast': True,
        'dialog': False,
        'quiet': False,
    },
    'autostart': {
        'method': 'startup',
        'args': '--watch --quiet',
    },
    'auto': {
        'enabled': False,
        'user_id': '',
        'password': '',
        'service': '',
    },
}


# --------------------------------------------------------------------------- #
# 路径处理
# --------------------------------------------------------------------------- #
def program_dir() -> str:
    """程序所在目录：打包后是 exe 所在目录，否则是项目根目录。"""

    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_config_path() -> str:
    """默认配置文件路径。

    优先级：``RUJIE_EPORTA_CONFIG`` 环境变量 > exe/项目目录 > 当前工作目录。
    """

    env_path = os.environ.get(ENV_CONFIG_PATH)
    if env_path:
        return os.path.abspath(env_path)

    exe_dir = program_dir()
    candidates = [os.path.join(exe_dir, CONFIG_FILENAME), os.path.join(os.getcwd(), CONFIG_FILENAME)]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]


def write_template(path: Optional[str] = None) -> str:
    """写入一份带注释的配置模板，返回写入的路径。"""

    path = os.path.abspath(path or default_config_path())
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(DEFAULT_CONFIG_TEMPLATE)
    return path


# --------------------------------------------------------------------------- #
# 合并 / 归一化 / 校验
# --------------------------------------------------------------------------- #
def _deep_merge(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def stringify_leaf_values(target: Dict[str, Any]) -> Dict[str, Any]:
    """把表单字段里的 bool/int/None 统一转成字符串（沿用上游行为）。"""

    for key, value in list(target.items()):
        if isinstance(value, dict):
            stringify_leaf_values(value)
        elif isinstance(value, bool):
            target[key] = str(value).lower()
        elif isinstance(value, int):
            target[key] = str(value)
        elif value is None:
            target[key] = ''
    return target


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(result, minimum)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_probe_list(value: Any, default: List[Any]) -> List[Any]:
    """把配置里的探测项规范成列表。

    支持两种写法：纯 URL 字符串，或 ``{url: ..., contains: ..., expect_status: ...}`` 映射。
    """

    if isinstance(value, str):
        items: List[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return copy.deepcopy(default)

    probes: List[Any] = []
    for item in items:
        if isinstance(item, dict):
            url = str(item.get('url') or '').strip()
            if not url:
                continue
            probe: Dict[str, Any] = {'url': url}
            contains = str(item.get('contains') or '').strip()
            if contains:
                probe['contains'] = contains
            expect = item.get('expect_status', item.get('expect'))
            if isinstance(expect, int):
                probe['expect_status'] = [int(expect)]
            elif isinstance(expect, (list, tuple)) and expect:
                probe['expect_status'] = [int(code) for code in expect]
            probes.append(probe)
        else:
            text = str(item or '').strip()
            if text:
                probes.append(text)
    return probes or copy.deepcopy(default)


def _normalize_options(cfg: Dict[str, Any]) -> None:
    daemon = cfg['daemon']
    daemon['check_interval'] = _as_int(daemon.get('check_interval'), 30, 1)
    daemon['keepalive_interval'] = _as_int(daemon.get('keepalive_interval'), 0, 0)
    daemon['retry_interval'] = _as_int(daemon.get('retry_interval'), 5, 1)
    daemon['max_retry_interval'] = _as_int(daemon.get('max_retry_interval'), 300, 1)
    if daemon['max_retry_interval'] < daemon['retry_interval']:
        daemon['max_retry_interval'] = daemon['retry_interval']
    daemon['cooldown_after_failures'] = _as_int(daemon.get('cooldown_after_failures'), 0, 0)
    daemon['cooldown'] = _as_int(daemon.get('cooldown'), 180, 0)
    daemon['login_timeout'] = _as_int(daemon.get('login_timeout'), 10, 1)
    daemon['probe_timeout'] = _as_int(daemon.get('probe_timeout'), 3, 1)
    daemon['log_file'] = str(daemon.get('log_file') or '')
    daemon['log_max_bytes'] = _as_int(daemon.get('log_max_bytes'), 1048576, 0)
    daemon['log_backup_count'] = _as_int(daemon.get('log_backup_count'), 3, 0)

    network = cfg['network']
    network['probes'] = _as_probe_list(network.get('probes'), DEFAULT_OPTIONS['network']['probes'])
    network['timeout'] = _as_int(network.get('timeout'), daemon['probe_timeout'], 1)

    notify = cfg['notify']
    notify['toast'] = _as_bool(notify.get('toast'), True)
    notify['dialog'] = _as_bool(notify.get('dialog'), False)
    notify['quiet'] = _as_bool(notify.get('quiet'), False)

    autostart = cfg['autostart']
    method = str(autostart.get('method') or 'startup').strip().lower()
    autostart['method'] = method if method in ('startup', 'registry') else 'startup'
    autostart['args'] = str(autostart.get('args') or '--watch --quiet')

    auto = cfg['auto']
    auto['enabled'] = _as_bool(auto.get('enabled'), False)
    auto['user_id'] = str(auto.get('user_id') or '').strip()
    auto['password'] = str(auto.get('password') or '')
    auto['service'] = str(auto.get('service') or '').strip()


def _validate_version(cfg: Dict[str, Any]) -> None:
    main = cfg.get('main') or {}
    version = main.get('version', 0)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 0
    if version < CONFIG_VERSION:
        raise ConfigError(
            '配置文件格式过期（version=%s，当前需要 %s）。\n'
            '请备份并删除原配置文件后重新运行本程序生成新配置。' % (version, CONFIG_VERSION)
        )
    if version > CONFIG_VERSION:
        raise ConfigError(
            '配置文件版本（version=%s）高于本程序支持的版本（%s），请更新程序。' % (version, CONFIG_VERSION)
        )


def _validate(cfg: Dict[str, Any]) -> None:
    auto = cfg.get('auto') or {}
    login_data = cfg.get('login_data') or {}

    if bool(auto.get('enabled')):
        # 免抓包模式：账号密码来自 auto 段，url / cookie / queryString 都不需要填
        if not str(auto.get('user_id') or '').strip() and not str(login_data.get('userId') or '').strip():
            raise ConfigError('免抓包模式需要填写 auto.user_id（你的学号）。')
        if not str(auto.get('password') or '').strip():
            raise ConfigError('免抓包模式需要填写 auto.password（你的密码）。')
        if not cfg['network']['probes']:
            raise ConfigError('配置项 network.probes 至少要保留一个探测地址。')
        return

    url = cfg.get('url') or {}
    if not str(url.get('server') or '').strip():
        raise ConfigError('配置项 url.server 不能为空。')
    if not str(url.get('login') or '').strip():
        raise ConfigError('配置项 url.login 不能为空。')

    if str(login_data.get('userId') or '') == PLACEHOLDER_USER_ID:
        raise ConfigError('配置文件未正确填写：login_data.userId 仍是占位值，请填写你的账号。')
    if not str(login_data.get('userId') or '').strip():
        raise ConfigError('配置文件未正确填写：login_data.userId 不能为空。')

    if not cfg['network']['probes']:
        raise ConfigError('配置项 network.probes 至少要保留一个探测地址。')


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def load_config(path: Optional[str] = None, *, create_if_missing: bool = True) -> Dict[str, Any]:
    """读取并校验配置，返回合并了默认值的字典。

    ``cfg['_config_path']`` 保存实际使用的配置文件路径。
    """

    config_path = os.path.abspath(path or default_config_path())

    if not os.path.isfile(config_path):
        if not create_if_missing:
            raise ConfigError('配置文件不存在：%s' % config_path)
        written = write_template(config_path)
        raise ConfigError('配置文件不存在，已生成配置模板：\n%s\n请填写账号信息后重新运行。' % written)

    try:
        with open(config_path, 'r', encoding='utf-8') as fp:
            raw = yaml.safe_load(fp)
    except yaml.YAMLError as exc:
        raise ConfigError('配置文件 YAML 解析失败：\n%s' % exc) from exc
    except OSError as exc:
        raise ConfigError('配置文件读取失败：\n%s' % exc) from exc

    if raw is None:
        raise ConfigError('配置文件内容为空：%s' % config_path)
    if not isinstance(raw, dict):
        raise ConfigError('配置文件根节点必须是映射（key: value）结构：%s' % config_path)

    _validate_version(raw)

    cfg = _deep_merge(DEFAULT_OPTIONS, raw)
    cfg.setdefault('funtion', {})
    cfg['funtion'].setdefault('check_school_network', True)
    cfg['funtion'].setdefault('disconnect_network', True)
    cfg['funtion']['check_school_network'] = _as_bool(cfg['funtion'].get('check_school_network'), True)
    cfg['funtion']['disconnect_network'] = _as_bool(cfg['funtion'].get('disconnect_network'), True)

    for key in ('url', 'login_data', 'logout_data'):
        value = cfg.get(key)
        if isinstance(value, dict):
            cfg[key] = stringify_leaf_values(value)

    if cfg.get('logout_data') is None:
        cfg['logout_data'] = {}
    if not isinstance(cfg.get('logout_data'), dict):
        cfg['logout_data'] = {}
    if cfg.get('headers') is None:
        cfg['headers'] = {}
    if not isinstance(cfg.get('headers'), dict):
        cfg['headers'] = {}
    cfg['cookie'] = str(cfg.get('cookie') or '')

    server = str(cfg['url'].get('server') or '').strip()
    cfg['url']['server'] = server.rstrip('/')

    _normalize_options(cfg)
    _validate(cfg)

    cfg['_config_path'] = config_path
    cfg.setdefault('main', {})
    cfg['main']['version'] = CONFIG_VERSION
    return cfg
