#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网状态探测与校园网服务器可达性判断。

判定规则（比上游只看状态码更可靠）：

* ``generate_204`` 类探测要求返回 204；
* 其它地址接受 2xx（200-208 / 226）；
* **若被重定向到别的域名或别的路径，视为被认证门户劫持 → 未联网**
  （校园网未认证时，网关会把请求 302 到认证页，最终仍是 200）；
* 可选用 ``contains`` 校验响应正文关键字（例如微软连通性测试的
  ``Microsoft Connect Test``），进一步排除劫持页面。

探测地址支持两种写法：

.. code-block:: yaml

    network:
      probes:
        - http://connect.rom.miui.com/generate_204
        - url: http://www.msftconnecttest.com/connecttest.txt
          contains: Microsoft Connect Test
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

log = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.39'
)

DEFAULT_PROBES: List[Any] = [
    'http://connect.rom.miui.com/generate_204',
    {
        'url': 'http://www.msftconnecttest.com/connecttest.txt',
        'contains': 'Microsoft Connect Test',
    },
]

MAX_BODY_BYTES = 8192


@dataclass(frozen=True)
class ProbeSpec:
    """单个探测规则。"""

    url: str
    expect_status: Tuple[int, ...] = ()
    contains: str = ''

    @classmethod
    def from_value(cls, value: Union[str, dict, 'ProbeSpec', None]) -> 'ProbeSpec':
        if isinstance(value, ProbeSpec):
            return value
        if isinstance(value, dict):
            url = str(value.get('url') or '').strip()
            contains = str(value.get('contains') or '')
            raw_expect = value.get('expect_status', value.get('expect'))
            expect: Tuple[int, ...] = ()
            if isinstance(raw_expect, int):
                expect = (int(raw_expect),)
            elif isinstance(raw_expect, (list, tuple)):
                expect = tuple(int(item) for item in raw_expect)
            return cls(url, expect, contains)
        return cls(str(value or '').strip())

    @property
    def expects_204(self) -> bool:
        return urllib.parse.urlparse(self.url).path.rstrip('/').endswith('generate_204')


@dataclass
class ProbeResult:
    """单次联网探测的结果。"""

    online: bool
    url: str = ''
    status: Optional[int] = None
    error: Optional[str] = None

    @property
    def detail(self) -> str:
        if self.error:
            return '探测 %s 失败：%s' % (self.url or '-', self.error)
        return '探测 %s 返回 HTTP %s' % (self.url or '-', self.status)


def _build_request(url: str, method: str = 'GET') -> urllib.request.Request:
    return urllib.request.Request(
        url,
        method=method,
        headers={
            'User-Agent': USER_AGENT,
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Accept': '*/*',
        },
    )


def _is_redirected(original: str, final: str) -> bool:
    """判断请求是否被重定向到了其它域名或其它路径（门户劫持特征）。"""

    if not final or not original:
        return False
    first = urllib.parse.urlsplit(original)
    last = urllib.parse.urlsplit(final)
    if first.hostname != last.hostname:
        return True
    return first.path.rstrip('/') != last.path.rstrip('/')


def probe_once(spec: Union[str, dict, ProbeSpec], timeout: float = 3.0) -> ProbeResult:
    """按探测规则访问一次，判断当前是否真的能访问外网。"""

    rule = ProbeSpec.from_value(spec)
    if not rule.url:
        return ProbeResult(False, '', None, '探测地址为空')

    status: Optional[int] = None
    final_url = rule.url
    body = b''
    need_body = bool(rule.contains)

    try:
        with urllib.request.urlopen(_build_request(rule.url), timeout=timeout) as resp:
            status = getattr(resp, 'status', None) or resp.getcode()
            final_url = resp.geturl() or rule.url
            if need_body:
                body = resp.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        status = exc.code
        final_url = getattr(exc, 'url', rule.url) or rule.url
        try:
            exc.close()
        except Exception:  # pragma: no cover
            pass
    except Exception as exc:  # URLError / socket.timeout / ssl 错误等
        return ProbeResult(False, rule.url, None, '%s: %s' % (type(exc).__name__, exc))

    if status is None:
        return ProbeResult(False, rule.url, None, '未获取到状态码')

    if rule.expect_status:
        online = status in rule.expect_status
    elif rule.expects_204:
        online = status == 204
    else:
        online = 200 <= status <= 208 or status == 226

    if online and need_body:
        text = body.decode('utf-8', errors='replace')
        if rule.contains not in text:
            return ProbeResult(False, rule.url, status, '响应内容中不含关键字 %r（可能被认证门户劫持）' % rule.contains)

    if online and _is_redirected(rule.url, final_url):
        return ProbeResult(False, rule.url, status, '请求被重定向到 %s（疑似认证门户拦截）' % final_url)

    return ProbeResult(online, rule.url, status, None)


def is_online(probes: Optional[Sequence[Any]] = None, timeout: float = 3.0) -> ProbeResult:
    """依次尝试所有探测规则，任一成功即认为已联网。"""

    candidates: Iterable[Any] = probes or DEFAULT_PROBES
    last = ProbeResult(False, '', None, '未配置探测地址')
    for item in candidates:
        result = probe_once(item, timeout)
        if result.online:
            return result
        last = result
    return last


def can_reach(server: str, timeout: float = 3.0) -> bool:
    """判断校园网认证服务器是否可达（用于"是否在校园网环境"）。"""

    if not server:
        return False
    try:
        with urllib.request.urlopen(_build_request(server), timeout=timeout) as resp:
            resp.read(1)
        return True
    except urllib.error.HTTPError:
        # 服务器给出了 HTTP 响应（含 4xx/5xx）即说明网络可达
        return True
    except Exception as exc:
        log.debug('校园网服务器 %s 不可达：%s: %s', server, type(exc).__name__, exc)
        return False
