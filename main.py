#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyInstaller 打包入口（保持顶层脚本，避免相对导入问题）。"""

from __future__ import annotations

import sys

from campus_network_launcher.cli import main

if __name__ == '__main__':
    sys.exit(main())
