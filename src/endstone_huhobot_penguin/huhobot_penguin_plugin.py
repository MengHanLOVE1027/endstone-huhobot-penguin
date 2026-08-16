"""
HuHoBotPenguin — EndStone Python 插件入口。

QQ 开放平台官方机器人（WebSocket 接入）与 Minecraft 基岩版服务器之间的
聊天 / 命令桥接插件。由 Java Spigot 版 HuHoBotPenguin 移植而来，功能平级。

线程模型：
    - 主线程：事件（PlayerChat/Join/Quit）、scheduler 任务、dispatch_command / broadcast_message
    - 后台线程：QQ 网关读循环 / 心跳 / 发送队列
    - 跨线程：run_on_main()（scheduler.run_task + Event 等待）把 BDS 命令/广播调度回主线程
"""

import os
import threading
import time

# endstone 库
from endstone.event import PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent, event_handler
from endstone.plugin import Plugin

# 插件内部模块
from . import bot as bot_mod
from . import commands as commands_mod
from . import config as config_mod
from . import custom_commands as custom_commands_mod
from . import qqclient as qqclient_mod
from . import state as state_mod
from .logger import log

PLUGIN_NAME = "huhobot-penguin"
PLUGIN_VERSION = "0.1.0-beta.1"
PLUGIN_DESCRIPTION = "QQ 开放平台官方机器人与 BDS 之间的聊天 / 命令桥接插件（EndStone Python 版）"
PLUGIN_AUTHORS = ["huohua", "MengHanLOVE1027"]


class HuHoBotPenguinPlugin(Plugin):
    api_version = "0.5"

    name = PLUGIN_NAME
    version = PLUGIN_VERSION
    description = PLUGIN_DESCRIPTION
    authors = PLUGIN_AUTHORS

    commands = {
        "huhobot": {
            "description": "HuHoBotPenguin 控制台命令（reload 重载配置 / info 查看信息）",
            "usages": ["/huhobot reload", "/huhobot info"],
            "permissions": ["huhobot_penguin.command.use"],
        },
    }

    permissions = {
        "huhobot_penguin.command.use": {
            "description": "允许使用 /huhobot 命令",
            "default": "op",
        },
    }

    def __init__(self):
        super().__init__()
        self._main_thread = None
        self.cfg = None
        self.state = None
        self.custom = None
        self.client = None
        self.bot = None
        self._recent_forwards = []
        self._forward_lock = threading.Lock()

    # ---- 生命周期 ----

    def on_load(self):
        self._main_thread = threading.get_ident()

    def on_enable(self):
        self._start_runtime()
        self.register_events(self)

    def on_disable(self):
        self._stop_runtime()

    # ---- 运行时管理 ----

    def _data_dir(self):
        root = str(self.data_folder)
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            pass
        return root

    def _start_runtime(self):
        root = self._data_dir()
        self.cfg = config_mod.load(root)
        app_id = self.cfg.get_string("bot.app-id", "")
        secret = self.cfg.get_string("bot.secret", "")
        if not app_id or not secret:
            log.warning("未配置 bot.app-id / bot.secret，QQ 机器人未启动。"
                        "请编辑 " + root + "/config.json")
            return
        self.state = state_mod.State(root, self.cfg)
        self.custom = custom_commands_mod.CustomCommands(self.cfg)
        self.client = qqclient_mod.QQClient(self.cfg)
        self.bot = bot_mod.Bot(self.cfg, self.state, self.client, self.custom, self)
        self.client.on_group_message = self._handle_group_message
        self.client.start()
        log.info("HuHoBot Penguin 已加载（v" + self.version + "）")

    def _stop_runtime(self):
        client = self.client
        self.client = None
        self.bot = None
        if client:
            try:
                client.stop()
            except Exception as e:
                log.error("停止机器人出错：" + str(e))
        self.state = None
        self.custom = None
        self.cfg = None

    # ---- 跨线程调度 ----

    def run_on_main(self, func, timeout=30):
        """把 func 调度到主线程执行并同步等待结果。已在主线程则直接执行。"""
        if threading.get_ident() == self._main_thread:
            return func()
        done = threading.Event()
        holder = {}

        def _wrapper():
            try:
                holder["value"] = func()
            except Exception as e:
                holder["error"] = e
            finally:
                done.set()

        try:
            self.server.scheduler.run_task(self, _wrapper, 0, 0)
        except Exception as e:
            log.error("run_on_main 调度失败：" + str(e))
            return None
        if not done.wait(timeout):
            log.error("run_on_main 超时（主线程未响应）")
            return None
        if "error" in holder:
            raise holder["error"]
        return holder.get("value")

    # ---- QQ → 游戏 ----

    def _handle_group_message(self, message):
        try:
            commands_mod.handle_group_message(self.bot, message)
        except Exception:
            log.exception("处理群消息出错")

    # ---- 游戏 → QQ ----

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        if not self.bot:
            return
        player = getattr(event, "player", None)
        name = getattr(player, "name", None) or "未知"
        self._forward_game_message(name, getattr(event, "message", ""))

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        self._notify_join_leave(getattr(event, "player", None), True)

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        self._notify_join_leave(getattr(event, "player", None), False)

    def _forward_game_message(self, player_name, raw_msg):
        """转发以 chat-format.start-with 开头（默认 #）的游戏聊天到所有已配置群，带 1.5s 去重。"""
        if not self.bot:
            return
        start_with = self.cfg.get_string("chat-format.start-with", "")
        raw = str(raw_msg or "")
        if not raw or (start_with and not raw.startswith(start_with)):
            return
        content = raw[len(start_with):] if start_with else raw
        key = str(player_name or "") + "\n" + content
        now = time.time()
        with self._forward_lock:
            self._recent_forwards = [e for e in self._recent_forwards if now - e["ts"] <= 1.5]
            if any(e["key"] == key for e in self._recent_forwards):
                return
            self._recent_forwards.append({"key": key, "ts": now})

        # 审计（可能含 AI 二审网络请求）放到后台线程，避免阻塞主线程
        bot = self.bot

        def _send():
            try:
                filtered = bot.audit_text(content)
                bot.send_to_all_groups(bot.format_game_message(player_name, filtered))
            except Exception:
                log.exception("转发游戏消息失败")

        threading.Thread(target=_send, daemon=True).start()

    def _notify_join_leave(self, player, is_join):
        if not self.bot or not self.cfg.get_bool("join-leave.enabled", True):
            return
        name = getattr(player, "name", None) or "未知"
        server_name = self.cfg.get_string("serverName", "") or self.cfg.get_string("bot.name", "HuHoBot")
        if is_join:
            fmt_key = "join-leave.join-format"
            def_fmt = "[{server}] 🟢{name}进入服务器"
        else:
            fmt_key = "join-leave.leave-format"
            def_fmt = "[{server}] 🔴{name}退出服务器"
        text = self.cfg.get_string(fmt_key, def_fmt).replace("{server}", server_name).replace("{name}", name)
        self.bot.send_to_all_groups(text)

    # ---- 控制台命令 ----

    def on_command(self, sender, command, args):
        if command.name != "huhobot":
            return False
        sub = str(args[0]).lower() if args else ""
        if sub == "reload":
            log.info("正在重载配置…")
            self._stop_runtime()
            self._start_runtime()
            sender.send_message("已重载配置文件。")
        elif sub == "info":
            sender.send_message("平台：EndStone（Python 后端）\n版本：v" + self.version + "\n模式：直连 QQ 正式环境")
        else:
            sender.send_message("用法：/huhobot reload | /huhobot info")
        return True
