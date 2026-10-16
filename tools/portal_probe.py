#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读侦察脚本：分析学校 ePortal 认证页结构，供「免抓包」功能适配使用。

它只会发送 GET 请求（认证页与其中的 JS 文件），**不会发送任何登录请求**，
因此不会影响你当前的联网状态。

用法：
    # 用已有配置里的 Referer / queryString 打开真实认证页（推荐）
    .venv\\Scripts\\python.exe tools\\portal_probe.py --config "D:\\path\\config.yml"

    # 或者只探测服务器（不带认证参数，通常会被拒绝）
    .venv\\Scripts\\python.exe tools\\portal_probe.py --server http://172.18.117.201
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
KEYWORDS = [
    'encrypt',
    'password',
    'AES',
    'CryptoJS',
    'RSA',
    'publicKey',
    'queryString',
    'validcode',
    'service',
    'login',
]
PROBE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'eporta_probe')


def fetch(url: str, timeout: float = 10.0):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            text = body.decode('gbk', 'replace')
        return response.status, response.geturl(), text, dict(response.headers)


def snippets(text: str, keyword: str, window: int = 240, limit: int = 3):
    found = []
    lowered = text.lower()
    start = 0
    while len(found) < limit:
        index = lowered.find(keyword.lower(), start)
        if index < 0:
            break
        chunk = text[max(0, index - window // 3): index + window]
        found.append(' '.join(chunk.split()))
        start = index + len(keyword)
    return found


def analyse(text: str, label: str) -> None:
    print('\n### %s（%d 字符）' % (label, len(text)))
    for keyword in KEYWORDS:
        for snippet in snippets(text, keyword):
            print('  [%s] %s' % (keyword, snippet[:420]))


def save(name: str, text: str) -> str:
    os.makedirs(PROBE_DIR, exist_ok=True)
    path = os.path.join(PROBE_DIR, re.sub(r'[^A-Za-z0-9_.-]', '_', name))
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(text)
    return path


def portal_url_from_config(config_path: str) -> tuple:
    import yaml

    with open(config_path, 'r', encoding='utf-8') as fp:
        cfg = yaml.safe_load(fp)
    server = str(cfg['url']['server']).rstrip('/')
    referer = str((cfg.get('headers') or {}).get('Referer') or '')
    query = str((cfg.get('login_data') or {}).get('queryString') or '')
    if referer:
        return referer, server
    if query:
        return '%s/eportal/index.jsp?%s' % (server, query), server
    return '%s/eportal/index.jsp' % server, server


def main() -> int:
    parser = argparse.ArgumentParser(description='ePortal 认证页只读侦察')
    parser.add_argument('--config', help='从已有 config.yml 读取认证参数（推荐）')
    parser.add_argument('--server', default=os.environ.get('EPORTA_SERVER', 'http://172.18.117.201'))
    args = parser.parse_args()

    if args.config:
        url, server = portal_url_from_config(args.config)
        print('从配置构造认证页地址：%s' % url[:160])
    else:
        server = args.server.rstrip('/')
        url = server + '/eportal/index.jsp'

    print('=' * 70)
    try:
        status, final, html, headers = fetch(url)
    except Exception as exc:
        print('请求失败：%s: %s' % (type(exc).__name__, exc))
        return 1

    print('状态码：%s   最终地址：%s   长度：%d' % (status, final, len(html)))
    cookie = headers.get('Set-Cookie') or headers.get('set-cookie')
    if cookie:
        print('Set-Cookie：%s' % cookie[:300])
    print('--- 内容开头 800 字符 ---')
    print(html[:800])
    save('index.html', html)

    scripts = re.findall(r'<script[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.I)
    print('\n### 引用的外部脚本（%d 个）' % len(scripts))
    for src in scripts:
        target = src if src.lower().startswith('http') else server + '/' + src.lstrip('/')
        try:
            _status, _final, text, _headers = fetch(target)
        except Exception as exc:
            print('  - %s  下载失败：%s' % (src, exc))
            continue
        path = save(src.split('/')[-1] or 'script.js', text)
        print('  - %s  → %s（%d 字符）' % (src, path, len(text)))
        analyse(text, 'JS: %s' % src)

    inline = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', html, re.S | re.I)
    for index, code in enumerate(inline):
        if len(code.strip()) < 20:
            continue
        path = save('inline_%d.js' % index, code)
        print('\n### 内联脚本 %d → %s' % (index, path))
        analyse(code, '内联脚本 %d' % index)

    print('\n### 侦察文件目录：%s' % PROBE_DIR)
    print(json.dumps(sorted(os.listdir(PROBE_DIR)), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
