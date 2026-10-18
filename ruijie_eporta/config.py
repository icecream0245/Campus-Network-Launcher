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
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONFIG_VERSION = 3
CONFIG_FILENAME = 'config.yml'
PLACEHOLDER_USER_ID = '00000000000'
ENV_CONFIG_PATH = 'RUJIE_EPORTA_CONFIG'

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置读取或校验失败。"""


class ProfileIncompleteError(ConfigError):
    """配置里还没有账号信息（首次运行需要引导用户填写）。"""


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
  service_options: [] # 上次获取到的可选服务商（程序自动维护，可不填）
# 想把这些参数固化进配置文件（等价于自动抓包），可运行：--auto-setup
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
        'service_options': [],
    },
}


# --------------------------------------------------------------------------- #
# 路径处理
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 自动配置：把探测结果写入配置文件
# --------------------------------------------------------------------------- #
DISCOVERED_HEADER = """\
# ============================================================================
#  锐捷 ePortal 校园网自动登录工具 —— 由程序自动探测生成的配置
#
#  生成时间：{time}
#  认证服务器：{server}
#  运营商    ：{service}
#
#  说明：
#    * 下面的 url / cookie / login_data / headers 是本程序访问学校认证页
#      自动取得的参数，等价于一次"抓包"的结果；其中 password 是按本机 MAC
#      加密后的密文，queryString 与本机 IP/MAC 绑定。
#    * 换网络环境后若登录失败，可执行 --auto-setup 重新生成，
#      或把 auto.enabled 设为 true 让程序每次登录都自动重新探测（最省心）。
#    * ⚠️ 本文件含账号与密码信息，请勿外发。
# ============================================================================

"""


def render_config_text(cfg: Dict[str, Any], *, header: str = '') -> str:
    """把配置字典渲染成 YAML 文本（保留字段顺序、支持中文）。"""

    body = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
    return header + body


def build_config_from_discovery(discovered: Dict[str, Any], *, auto_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """由自动探测结果构造一份完整、可写入的配置。"""

    cfg: Dict[str, Any] = {
        'main': {'version': CONFIG_VERSION},
        'funtion': {
            'check_school_network': True,
            'disconnect_network': True,
        },
        'url': dict(discovered.get('url') or {}),
        'cookie': str(discovered.get('cookie') or ''),
        'login_data': dict(discovered.get('login_data') or {}),
        'logout_data': {},
        'headers': dict(discovered.get('headers') or {}),
    }
    for key, value in DEFAULT_OPTIONS.items():
        if key == 'auto':
            continue
        cfg[key] = copy.deepcopy(value)
    cfg['auto'] = dict(auto_cfg or DEFAULT_OPTIONS['auto'])
    return cfg


def save_config(
    path: str,
    cfg: Dict[str, Any],
    *,
    header: str = '',
    backup: bool = True,
) -> Tuple[str, str]:
    """把配置写入文件（先备份，再原子替换）。

    Returns:
        Tuple[str, str]: (写入的配置文件路径, 备份文件路径；未备份时为空串)
    """

    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    backup_path = ''
    if backup and os.path.isfile(target):
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_path = '%s.bak-%s' % (target, stamp)
        try:
            shutil.copy2(target, backup_path)
        except OSError as exc:
            raise ConfigError('备份原配置失败（%s）：%s' % (target, exc)) from exc

    text = render_config_text(cfg, header=header)
    temp_path = target + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as fp:
            fp.write(text)
        os.replace(temp_path, target)
    except OSError as exc:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise ConfigError('写入配置文件失败（%s）：%s' % (target, exc)) from exc
    return target, backup_path


# --------------------------------------------------------------------------- #
# 首次运行：账号是否已配置
# --------------------------------------------------------------------------- #
def is_profile_incomplete(cfg: Dict[str, Any]) -> bool:
    """判断配置里是否还没有可用的账号信息。

    只要存在抓包参数（queryString），就认为配置可用（老用户的老配置）。
    """

    login_data = cfg.get('login_data') or {}
    if str(login_data.get('queryString') or '').strip():
        return False

    auto = cfg.get('auto') or {}
    user_id = str(auto.get('user_id') or login_data.get('userId') or '').strip()
    password = str(auto.get('password') or '').strip()
    if not user_id or user_id == PLACEHOLDER_USER_ID:
        return True
    return not password


def build_profile_fields(
    user_id: str,
    password: str,
    service: str,
    options: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """生成向导保存时要写入 auto 段的字段。"""

    fields: Dict[str, Any] = {
        'enabled': True,
        'user_id': str(user_id or '').strip(),
        'password': str(password or ''),
        'service': str(service or '').strip(),
    }
    if options is not None:
        fields['service_options'] = [str(item).strip() for item in options if str(item or '').strip()]
    return fields


# --------------------------------------------------------------------------- #
# 局部写回：只改 auto 段，保留文件其余内容与注释
# --------------------------------------------------------------------------- #
AUTO_FIELD_ORDER = ('enabled', 'user_id', 'password', 'service', 'service_options')


def _format_auto_value(key: str, value: Any) -> str:
    if key == 'enabled':
        return 'true' if value else 'false'
    if key == 'service_options':
        items = [str(item).strip() for item in (value or []) if str(item or '').strip()]
        if not items:
            return '[]'
        return '[' + ', '.join("'%s'" % item.replace("'", "''") for item in items) + ']'
    return "'%s'" % str(value if value is not None else '').replace("'", "''")


def update_auto_fields(path: str, fields: Dict[str, Any], *, backup: bool = True) -> Tuple[str, str]:
    """只修改 ``auto:`` 段内的若干键，保留文件其余内容与注释。

    Args:
        path: 配置文件路径。
        fields: 要写入的键值（仅接受 ``AUTO_FIELD_ORDER`` 中的键）。
        backup: 是否先备份原文件。

    Returns:
        Tuple[str, str]: (配置文件路径, 备份文件路径；未备份时为空串)

    Raises:
        ConfigError: 文件不存在、写入后 YAML 校验失败或写入失败（此时原文件不变）。
    """

    target = os.path.abspath(path)
    if not os.path.isfile(target):
        raise ConfigError('配置文件不存在：%s' % target)

    with open(target, 'r', encoding='utf-8') as fp:
        text = fp.read()

    updates = {key: value for key, value in (fields or {}).items() if key in AUTO_FIELD_ORDER}
    if not updates:
        raise ConfigError('没有需要写入的 auto 字段')

    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == 'auto:' and not line[:1].isspace():
            start = index
            break

    if start is None:
        # 没有 auto 段：追加一段
        block = ['', 'auto:']
        for key in AUTO_FIELD_ORDER:
            if key in updates:
                block.append('  %s: %s' % (key, _format_auto_value(key, updates[key])))
        new_text = text.rstrip() + chr(10) + chr(10).join(block) + chr(10)
    else:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if line.strip() and not line[:1].isspace():
                end = index
                break
        remaining = dict(updates)
        body = []
        for line in lines[start + 1:end]:
            match = re.match(r'^(\s+)([A-Za-z_][A-Za-z0-9_]*):(.*)$', line)
            if match and match.group(2) in remaining:
                key = match.group(2)
                body.append('%s%s: %s' % (match.group(1), key, _format_auto_value(key, remaining.pop(key))))
            else:
                body.append(line)
        for key in AUTO_FIELD_ORDER:
            if key in remaining:
                body.append('  %s: %s' % (key, _format_auto_value(key, remaining.pop(key))))
        new_text = '\n'.join(lines[:start + 1] + body + lines[end:]) + '\n'

    try:
        parsed = yaml.safe_load(new_text)
    except yaml.YAMLError as exc:
        raise ConfigError('写入后的配置无法解析，已放弃修改：%s' % exc) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get('auto'), dict):
        raise ConfigError('写入后的配置结构异常，已放弃修改')

    backup_path = ''
    if backup:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_path = '%s.bak-%s' % (target, stamp)
        try:
            shutil.copy2(target, backup_path)
        except OSError as exc:
            raise ConfigError('备份原配置失败：%s' % exc) from exc

    temp_path = target + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as fp:
            fp.write(new_text)
        os.replace(temp_path, target)
    except OSError as exc:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise ConfigError('写入配置文件失败：%s' % exc) from exc
    return target, backup_path


def discovered_header(discovered: Dict[str, Any]) -> str:
    """生成自动配置文件的头部注释。"""

    details = discovered.get('details') or {}
    return DISCOVERED_HEADER.format(
        time=time.strftime('%Y-%m-%d %H:%M:%S'),
        server=(discovered.get('url') or {}).get('server', '-'),
        service=details.get('service_used') or '-',
    )


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
    raw_options = auto.get('service_options')
    if isinstance(raw_options, str):
        raw_options = [raw_options]
    auto['service_options'] = [
        str(item).strip() for item in (raw_options or []) if str(item or '').strip()
    ]


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
            raise ProfileIncompleteError('免抓包模式需要填写 auto.user_id（你的学号）。')
        if not str(auto.get('password') or '').strip():
            raise ProfileIncompleteError('免抓包模式需要填写 auto.password（你的密码）。')
        if not cfg['network']['probes']:
            raise ConfigError('配置项 network.probes 至少要保留一个探测地址。')
        return

    url = cfg.get('url') or {}
    if not str(url.get('server') or '').strip():
        raise ConfigError('配置项 url.server 不能为空。')
    if not str(url.get('login') or '').strip():
        raise ConfigError('配置项 url.login 不能为空。')

    if str(login_data.get('userId') or '') == PLACEHOLDER_USER_ID:
        raise ProfileIncompleteError('配置文件未正确填写：login_data.userId 仍是占位值，请填写你的账号。')
    if not str(login_data.get('userId') or '').strip():
        raise ProfileIncompleteError('配置文件未正确填写：login_data.userId 不能为空。')

    if not cfg['network']['probes']:
        raise ConfigError('配置项 network.probes 至少要保留一个探测地址。')


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def load_config(
    path: Optional[str] = None,
    *,
    create_if_missing: bool = True,
    allow_incomplete: bool = False,
) -> Dict[str, Any]:
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
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}
        cfg[key] = stringify_leaf_values(cfg[key])

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
    if allow_incomplete:
        cfg['_needs_profile'] = is_profile_incomplete(cfg)
    else:
        _validate(cfg)

    cfg['_config_path'] = config_path
    cfg.setdefault('main', {})
    cfg['main']['version'] = CONFIG_VERSION
    return cfg
