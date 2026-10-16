#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免抓包：自动发现 ePortal 认证参数并完成登录。

原理（与认证页 JS 的行为一一对应）：

1. 访问一个必定被网关拦截的探测地址，**不跟随重定向**，从 ``Location`` 拿到
   认证页地址及其查询串（``wlanuserip`` / ``mac`` / ``nasid`` 等参数就在里面）；
2. 打开认证页，取得 ``JSESSIONID`` 会话；
3. 调用 ``InterFace.do?method=pageInfo`` 获取 ``publicKeyModulus`` /
   ``publicKeyExponent`` / ``passwordEncrypt`` 等信息；
4. 按认证页 ``security.js`` 的算法加密密码：明文为 ``密码 + ">" + mac``，
   整串**反转**后做**原生 RSA**（无 PKCS#1 填充）运算，输出十六进制；
5. 组装登录表单（``queryString`` 为原始查询串的单次 URL 编码）并 POST。

因此用户只需要提供学号与密码，无需再用浏览器抓包。
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import network
from .eportal import AuthResult

log = logging.getLogger(__name__)

PORTAL_PATH = '/eportal/index.jsp'
LOGIN_PATH = '/eportal/InterFace.do?method=login'
PAGE_INFO_PATH = '/eportal/InterFace.do?method=pageInfo'

USER_AGENT = network.USER_AGENT
DEFAULT_MAC = '111111111'
MAX_BODY_BYTES = 512 * 1024


class AutoLoginError(Exception):
    """免抓包流程中的可预期错误。"""


# --------------------------------------------------------------------------- #
# RSA 加密（复刻认证页 security.js 的实现）
# --------------------------------------------------------------------------- #
def rsa_encrypt_raw(plaintext: str, modulus_hex: str, exponent_hex: str = '10001') -> str:
    """按锐捷 ``security.js`` 的方式加密字符串。

    该实现是"教科书式 RSA"：把明文按 2 个字符一组拼成 16 位 digit（小端），
    直接做 ``pow(m, e, n)``，没有 PKCS#1 填充，输出十六进制。

    Args:
        plaintext: 待加密字符串（内部会按 JS 逻辑反转）。
        modulus_hex: 十六进制模数（来自 pageInfo）。
        exponent_hex: 十六进制指数，通常是 ``10001``。

    Returns:
        str: 十六进制密文；若明文超过一个分组，用空格分隔。
    """

    modulus_hex = (modulus_hex or '').strip().lower()
    exponent_hex = (exponent_hex or '10001').strip().lower()
    if not modulus_hex:
        raise AutoLoginError('pageInfo 未返回 RSA 模数（publicKeyModulus）')

    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16) if exponent_hex else 65537
    if modulus <= 0:
        raise AutoLoginError('RSA 模数无效')

    # JS 中 digit 是 16 位单位，chunkSize = 2 * (最高位 digit 索引)
    digits = max((len(modulus_hex) + 3) // 4, 2)
    chunk_size = 2 * (digits - 1)

    # 先反转字符串（JS: password.split("").reverse().join("")），再按字符码切块
    codes = [ord(ch) for ch in plaintext[::-1]]
    while codes and len(codes) % chunk_size:
        codes.append(0)
    if not codes:
        codes = [0] * chunk_size

    blocks: List[str] = []
    for start in range(0, len(codes), chunk_size):
        chunk = codes[start:start + chunk_size]
        value = 0
        for index in range(0, chunk_size, 2):
            digit = (chunk[index] & 0xFF) | ((chunk[index + 1] & 0xFF) << 8)
            value |= digit << (16 * (index // 2))
        encrypted = pow(value, exponent, modulus)
        blocks.append('%0*x' % (digits * 4, encrypted))
    return ' '.join(blocks)


def build_encrypted_password(password: str, mac: str, modulus_hex: str, exponent_hex: str = '10001') -> str:
    """生成登录表单里 ``password`` 字段的值。"""

    payload = '%s>%s' % (password, mac or DEFAULT_MAC)
    return rsa_encrypt_raw(payload, modulus_hex, exponent_hex)


def normalize_service(value: str) -> str:
    """把 service 规范成「单次 URL 编码」形式。

    认证页提交的 service 是 URL 编码后的字符串（例如
    ``%E4%B8%AD%E5%9B%BD%E7%94%B5%E4%BF%A1``）；由于我们随后还会用
    ``urlencode`` 组装表单，这里必须先编码一次，否则服务端收到的是原文而匹配不上。
    无论是传中文还是已编码的值，本函数都返回同样的单次编码结果（幂等）。
    """

    if not value:
        return ''
    return urllib.parse.quote(urllib.parse.unquote(value), safe='')


def choose_service(page_info: Dict[str, Any], preferred: str = '', html: str = '') -> str:
    """决定登录时使用的 ``service`` 值。

    ``pageInfo`` 会返回服务列表（形如 ``{"中国电信": {"serviceName": "中国电信", ...}}``），
    其中带 ``serviceDefault: true`` 的是服务器默认项（可能是"教师免费"之类的套餐）。

    优先级：配置里指定的名称（支持中文名 / 显示名 / 原始值）→ 服务器默认项 → 列表第一项。
    """

    services = page_info.get('service')
    if isinstance(services, dict) and services:
        entries = []
        for key, item in services.items():
            if isinstance(item, dict):
                entries.append(
                    (
                        str(key),
                        str(item.get('serviceName') or ''),
                        str(item.get('serviceShowName') or ''),
                        str(item.get('serviceDefault') or '').lower() == 'true',
                    )
                )
            else:
                entries.append((str(key), str(key), '', False))

        if preferred:
            target = urllib.parse.unquote(preferred).strip()
            for key, name, show, _default in entries:
                if target and target in (key, name, show):
                    return key
            return preferred
        for key, _name, _show, is_default in entries:
            if is_default:
                return key
        return entries[0][0]

    options = PortalDiscovery.parse_services(html)
    return preferred or (options[0] if options else '')


# --------------------------------------------------------------------------- #
# 探测与发现
# --------------------------------------------------------------------------- #
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁用自动跳转，以便读到网关给的 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


@dataclass
class PortalInfo:
    """一次认证会话所需的全部参数。"""

    portal_url: str
    server: str
    query_string: str
    encoded_query: str
    params: Dict[str, str] = field(default_factory=dict)

    @property
    def mac(self) -> str:
        return self.params.get('mac') or DEFAULT_MAC

    @property
    def login_url(self) -> str:
        return self.server + LOGIN_PATH

    @property
    def page_info_url(self) -> str:
        return self.server + PAGE_INFO_PATH


class PortalDiscovery:
    """负责"从探测请求一路走到可登录状态"。"""

    def __init__(self, *, probes: Optional[List[Any]] = None, timeout: float = 5.0, logger: Optional[logging.Logger] = None) -> None:
        self.probes = list(probes or network.DEFAULT_PROBES)
        self.timeout = float(timeout)
        self.log = logger or log
        self._jar = http.cookiejar.CookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self._jar)
        self._opener = urllib.request.build_opener(cookie_processor)
        # 探测重定向时必须禁用自动跳转，否则读不到网关给出的 Location
        self._probe_opener = urllib.request.build_opener(_NoRedirect, cookie_processor)

    # ------------------------------------------------------------------ #
    def _base_headers(self, referer: str = '') -> Dict[str, str]:
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if referer:
            headers['Referer'] = referer
        return headers

    @staticmethod
    def _decode(body: bytes, content_type: str = '') -> str:
        for encoding in ('utf-8', 'gbk', 'latin-1'):
            try:
                return body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return body.decode('utf-8', 'replace')

    # ------------------------------------------------------------------ #
    def detect_location(self) -> Optional[str]:
        """请求探测地址，返回认证页 URL；已经在线时返回 None。"""

        last_error = ''
        for probe in self.probes:
            url = str(probe.get('url')) if isinstance(probe, dict) else str(probe)
            request = urllib.request.Request(url, headers=self._base_headers())
            location: Optional[str] = None
            status = 0
            body = b''
            try:
                with self._probe_opener.open(request, timeout=self.timeout) as response:
                    status = getattr(response, 'status', None) or response.getcode()
                    location = response.headers.get('Location')
                    body = response.read(8192)
            except urllib.error.HTTPError as exc:
                status = exc.code
                location = exc.headers.get('Location') if exc.headers else None
                try:
                    body = exc.read(8192)
                except Exception:  # pragma: no cover
                    body = b''
                try:
                    exc.close()
                except Exception:  # pragma: no cover
                    pass
            except Exception as exc:
                last_error = '%s: %s' % (type(exc).__name__, exc)
                self.log.debug('探测 %s 失败：%s', url, last_error)
                continue

            if location:
                # 相对地址要基于探测地址补全
                return urllib.parse.urljoin(url, location)

            # 少数网关不用 302，而是在 200 的页面里用 JS 跳转到认证页
            if status and body:
                text = self._decode(body)
                match = re.search(r'(https?://[^"\'\s<>]*?/eportal/[^"\'\s<>]*(?:wlanuserip|queryString)[^"\'\s<>]*)', text, re.I)
                if match:
                    self.log.debug('从响应正文中提取到认证页地址')
                    return match.group(1)

            if status == 204 or 200 <= status <= 208:
                # 已放行：说明当前已联网，无需认证
                return None

        if last_error:
            self.log.debug('所有探测地址都失败：%s', last_error)
        return ''

    def build_info(self, location: str) -> PortalInfo:
        """把认证页地址拆成登录所需的各个部分。"""

        split = urllib.parse.urlsplit(location)
        if not split.netloc:
            raise AutoLoginError('认证页地址无效：%s' % location)
        server = '%s://%s' % (split.scheme or 'http', split.netloc)
        query = split.query
        params = {key: values[0] for key, values in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
        return PortalInfo(
            portal_url=location,
            server=server,
            query_string=query,
            encoded_query=urllib.parse.quote(query, safe=''),
            params=params,
        )

    # ------------------------------------------------------------------ #
    def open_portal(self, info: PortalInfo) -> str:
        """打开认证页（顺带建立 JSESSIONID 会话），返回页面 HTML。"""

        request = urllib.request.Request(info.portal_url, headers=self._base_headers(info.server + '/'))
        with self._opener.open(request, timeout=self.timeout) as response:
            body = response.read(MAX_BODY_BYTES)
            content_type = response.headers.get('Content-Type', '')
        html = self._decode(body, content_type)
        self.log.debug('认证页已打开（%d 字符，%d 个 cookie）', len(html), len(self._jar))
        return html

    def page_info(self, info: PortalInfo) -> Dict[str, Any]:
        """调用 pageInfo 接口，获取公钥等认证参数。"""

        payload = urllib.parse.urlencode({'queryString': info.encoded_query}).encode('utf-8')
        headers = self._base_headers(info.portal_url)
        headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
        request = urllib.request.Request(info.page_info_url, data=payload, headers=headers, method='POST')
        with self._opener.open(request, timeout=self.timeout) as response:
            text = self._decode(response.read(MAX_BODY_BYTES))
        if not text.strip():
            raise AutoLoginError('pageInfo 返回空内容（认证页会话可能已失效）')
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AutoLoginError('pageInfo 返回内容无法解析为 JSON：%s' % ' '.join(text.split())[:160]) from exc
        if not isinstance(data, dict):
            raise AutoLoginError('pageInfo 返回结构异常')
        return data

    @staticmethod
    def parse_services(html: str) -> List[str]:
        """从认证页里解析运营商（service）下拉选项。"""

        match = re.search(r'<select[^>]*id\s*=\s*["\']service["\'][^>]*>(.*?)</select>', html, re.S | re.I)
        if not match:
            return []
        options = re.findall(r'<option[^>]*value\s*=\s*["\']([^"\']*)["\']', match.group(1), re.I)
        return [item for item in options if item.strip()]

    # ------------------------------------------------------------------ #
    def discover(self) -> Tuple[Optional[PortalInfo], str]:
        """走完"探测 → 认证页 → pageInfo"，返回 (info, html)。

        Returns:
            (None, '') 表示当前已经在线，无需认证。
        """

        location = self.detect_location()
        if location is None:
            return None, ''
        if not location:
            raise AutoLoginError('未能从网关获取认证页地址（可能不在校园网内）')
        info = self.build_info(location)
        html = self.open_portal(info)
        return info, html

    def login(
        self,
        user_id: str,
        password: str,
        service: str = '',
        *,
        info: Optional[PortalInfo] = None,
        html: str = '',
    ) -> AuthResult:
        """完成一次免抓包登录。"""

        if not user_id or not password:
            raise AutoLoginError('免抓包模式需要同时填写学号与密码')

        if info is None:
            info, html = self.discover()
        if info is None:
            return AuthResult(True, 'success', '当前已在线，无需认证')

        page = self.page_info(info)
        chosen_service = choose_service(page, service, html)
        service_value = normalize_service(chosen_service)
        modulus = str(page.get('publicKeyModulus') or '')
        exponent = str(page.get('publicKeyExponent') or '10001')
        encrypt_flag = 'true' if str(page.get('passwordEncrypt') or '').lower() == 'true' and modulus else 'false'

        if encrypt_flag == 'true':
            encrypted_password = build_encrypted_password(password, info.mac, modulus, exponent)
        else:
            encrypted_password = password

        form = {
            'userId': user_id,
            'password': encrypted_password,
            'service': service_value,
            'queryString': info.encoded_query,
            'operatorPwd': '',
            'operatorUserId': '',
            'validcode': '',
            'passwordEncrypt': encrypt_flag,
        }
        self.log.info(
            '免抓包：服务器=%s 加密=%s service=%s',
            info.server,
            encrypt_flag,
            chosen_service or '(空)',
        )
        return self._post_login(info, form)

    # ------------------------------------------------------------------ #
    # 自动配置：把发现到的参数整理成可写回 config.yml 的形式
    # ------------------------------------------------------------------ #
    def cookie_header(self) -> str:
        """把当前会话的 cookie 拼成 Cookie 头字符串。"""

        return '; '.join('%s=%s' % (cookie.name, cookie.value) for cookie in self._jar)

    def setup_config(self, user_id: str, password: str, service: str = '') -> Dict[str, Any]:
        """执行一次自动发现，返回可直接写入配置文件的参数。

        只访问认证页与 pageInfo，**不会发送登录请求**，因此不会影响当前联网状态。

        Raises:
            AutoLoginError: 当前已在线（拿不到认证页参数）或发现过程失败。
        """

        if not user_id or not password:
            raise AutoLoginError('自动配置需要学号与密码')

        info, html = self.discover()
        if info is None:
            raise AutoLoginError('当前已在线，拿不到认证页参数；请先断开校园网再执行自动配置')

        page = self.page_info(info)
        chosen_service = choose_service(page, service, html)
        modulus = str(page.get('publicKeyModulus') or '')
        exponent = str(page.get('publicKeyExponent') or '10001')
        encrypt = str(page.get('passwordEncrypt') or '').lower() == 'true' and bool(modulus)
        encrypted = build_encrypted_password(password, info.mac, modulus, exponent) if encrypt else password

        services = page.get('service')
        return {
            'url': {
                'server': info.server,
                'login': '/eportal/InterFace.do?method=login',
                'logout': '/eportal/InterFace.do?method=logout',
            },
            'cookie': self.cookie_header(),
            'login_data': {
                'userId': user_id,
                'password': encrypted,
                'service': normalize_service(chosen_service),
                'queryString': info.encoded_query,
                'operatorPwd': '',
                'operatorUserId': '',
                'validcode': '',
                'passwordEncrypt': 'true' if encrypt else 'false',
            },
            'headers': {'Referer': info.portal_url},
            'details': {
                'mac': info.mac,
                'portal_url': info.portal_url,
                'services': list(services.keys()) if isinstance(services, dict) else [],
                'service_used': chosen_service,
                'password_encrypted': encrypt,
                'modulus_bits': len(modulus) * 4 if modulus else 0,
            },
        }

    def _post_login(self, info: PortalInfo, form: Dict[str, str]) -> AuthResult:
        payload = urllib.parse.urlencode(form, doseq=True).encode('utf-8')
        headers = self._base_headers(info.portal_url)
        headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
        request = urllib.request.Request(info.login_url, data=payload, headers=headers, method='POST')

        try:
            with self._opener.open(request, timeout=max(self.timeout, 10)) as response:
                status = getattr(response, 'status', None) or response.getcode()
                text = self._decode(response.read(MAX_BODY_BYTES))
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                text = self._decode(exc.read(MAX_BODY_BYTES))
            except Exception:  # pragma: no cover
                text = ''
            self.log.warning('免抓包登录返回 HTTP %s', status)
        except Exception as exc:
            message = '%s: %s' % (type(exc).__name__, exc)
            self.log.warning('免抓包登录请求失败：%s', message)
            return AuthResult(False, status=None, error=message)

        try:
            parsed = json.loads(text)
        except ValueError:
            snippet = ' '.join(text.split())[:160]
            return AuthResult(False, status=status, raw=text, error='登录返回内容无法解析：%s' % (snippet or '<空>'))

        if not isinstance(parsed, dict):
            return AuthResult(False, status=status, raw=text, error='登录返回结构异常')
        result = parsed.get('result')
        message = str(parsed.get('message') or '')
        return AuthResult(result == 'success', result, message, status, parsed)
