"""
共享日志：使用 Python 标准 logging，输出到控制台（EndStone 会捕获 stdout/stderr）。
所有模块通过 `from .logger import log` 复用同一实例，避免到处传 logger。
"""

import logging

log = logging.getLogger("HuHoBotPenguin")

_configured = False


def setup_logger(level=logging.INFO):
    """在插件 on_load 时调用一次，配置控制台输出格式。"""
    global _configured
    if _configured:
        return
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[HuHoBotPenguin] [%(levelname)s] %(message)s"))
        log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    _configured = True
