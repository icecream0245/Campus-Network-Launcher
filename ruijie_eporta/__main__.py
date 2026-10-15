#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支持 ``python -m ruijie_eporta`` 方式运行。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
