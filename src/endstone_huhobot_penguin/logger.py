"""
共享日志（对齐 easybackuper 的「定制日志输出 + 按日文件日志」）：

    - 控制台：彩色 print() —— `[HuHoBotPenguin] [级别]` 头 + 渐变彩虹正文，默认只显示 INFO 及以上。
    - 文件：`<服务器根目录>/logs/HuHoBotPenguin/huhobot_penguin_<YYYYMMDD>.log`，UTF-8、DEBUG 级别、按日分割。

    关键点：文件用专用 logger（`logging.getLogger("HuHoBotPenguin")`）+ `propagate=False`，
    并在加载时先 `handlers.clear()`。因为 `logging` 模块会在 reload 时缓存同一个 logger
    （EndStone 只清理 sys.modules，不清理 logging 管理器），不清理就会残留旧的 StreamHandler，
    把日志再打到控制台一遍（「同一行出现两次」的根因），旧 FileHandler 还会叠加导致文件重复写入。
"""

import logging
import os
import random
import sys
import threading
import traceback
from datetime import datetime

PLUGIN_NAME = "HuHoBotPenguin"
PLUGIN_NAME_SMALLEST = "huhobot_penguin"

print_lock = threading.Lock()

# 级别 → (logging 级别, 控制台颜色)
_LEVELS = {
    "DEBUG": (logging.DEBUG, "\x1b[36m"),    # 青
    "INFO": (logging.INFO, "\x1b[37m"),      # 白
    "WARNING": (logging.WARNING, "\x1b[33m"),  # 黄
    "ERROR": (logging.ERROR, "\x1b[31m"),    # 红
    "SUCCESS": (logging.INFO, "\x1b[32m"),   # 绿
}

_CONSOLE_LEVEL = logging.INFO  # 控制台只显示 INFO 及以上


# ---- 渐变彩虹字 ----

def _random_vivid_color():
    rand = random.random() * 260
    if rand < 90:
        h = rand
    elif rand < 200:
        h = rand + 60
    else:
        h = rand + 100
    s = 0.90 + random.random() * 0.10
    l = 0.65 + random.random() * 0.15
    a = s * min(l, 1 - l)

    def f(n):
        k = (n + h / 30) % 12
        return round((l - a * max(-1, min(k - 3, 9 - k, 1))) * 255)

    return [f(0), f(8), f(4)]


def _generate_color_pair():
    c1 = _random_vivid_color()
    attempts = 0
    while True:
        c2 = _random_vivid_color()
        diff = abs(c1[0] - c2[0]) + abs(c1[1] - c2[1]) + abs(c1[2] - c2[2])
        if diff > 150 or attempts > 20:
            return [c1, c2]
        attempts += 1


_GLOBAL_C1, _GLOBAL_C2 = _generate_color_pair()


def _global_lerp_color(t):
    return [
        round(_GLOBAL_C1[0] + (_GLOBAL_C2[0] - _GLOBAL_C1[0]) * t),
        round(_GLOBAL_C1[1] + (_GLOBAL_C2[1] - _GLOBAL_C1[1]) * t),
        round(_GLOBAL_C1[2] + (_GLOBAL_C2[2] - _GLOBAL_C1[2]) * t),
    ]


def _random_gradient_color(text):
    length = len(text)
    out = ""
    for i in range(length):
        t = 0 if length <= 1 else i / (length - 1)
        r, g, b = _global_lerp_color(t)
        out += "\x1b[38;2;%d;%d;%dm%s" % (r, g, b, text[i])
    return out + "\x1b[0m"


class RandomColor:
    """随机渐变彩虹字。"""

    def __init__(self, text):
        self.text = text

    def __str__(self):
        return _random_gradient_color(self.text)


def _enable_windows_vt():
    """尽力在 Windows 控制台启用虚拟终端（VT），使 ANSI 颜色能被渲染。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint32]
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.SetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        for std in (-11, -12):  # STD_OUTPUT_HANDLE / STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(std)
            if not handle:
                continue
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


# ---- 文件日志 ----

def _setup_file_logging():
    log_dir = os.path.join(".", "logs", PLUGIN_NAME)
    try:
        os.makedirs(log_dir, exist_ok=True)
        filename = PLUGIN_NAME_SMALLEST + "_" + datetime.now().strftime("%Y%m%d") + ".log"
        handler = logging.FileHandler(os.path.join(log_dir, filename), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        _file_logger.addHandler(handler)
    except OSError:
        pass


_enable_windows_vt()

_file_logger = logging.getLogger(PLUGIN_NAME)
_file_logger.handlers.clear()  # 清除跨 reload 残留的 handler（避免重复打控制台 / 重复写文件）
_file_logger.setLevel(logging.DEBUG)
_file_logger.propagate = False
_setup_file_logging()


def plugin_print(text, level="INFO"):
    """彩色打印到控制台 + 写入插件日志文件。"""
    text = str(text)
    num_level, level_color = _LEVELS.get(level, (logging.INFO, "\x1b[37m"))
    if num_level >= _CONSOLE_LEVEL:
        logger_head = "[\x1b[93m" + PLUGIN_NAME + "\x1b[0m] [" + level_color + level + "\x1b[0m] "
        with print_lock:
            print(logger_head + str(RandomColor(text)))
    _file_logger.log(num_level, text)
    return True


class _LogShim:
    """让现有 log.debug/info/... 调用都走 plugin_print。"""

    def debug(self, msg):
        plugin_print(msg, "DEBUG")

    def info(self, msg):
        plugin_print(msg, "INFO")

    def warning(self, msg):
        plugin_print(msg, "WARNING")

    def error(self, msg):
        plugin_print(msg, "ERROR")

    def exception(self, msg):
        plugin_print(msg, "ERROR")
        if sys.exc_info()[0] is not None:
            tb = traceback.format_exc().rstrip("\n")
            with print_lock:
                print(tb)
            _file_logger.log(logging.ERROR, tb)


log = _LogShim()
