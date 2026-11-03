# -*- coding: utf-8 -*-
"""统一日志输出（行缓冲，多进程/重定向到文件时也能实时看到）"""

import time


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
