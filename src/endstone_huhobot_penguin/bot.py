"""
HuHoBot 门面：承载 auditText / sendCommand / broadcast / 消息格式化，供各模块引用。
对应 Java 版 HuHoBot interface + QClient。
"""

from endstone.command import CommandSenderWrapper
from endstone.lang import Translatable

from . import filter as filter_mod
from .logger import log


class Bot:
    def __init__(self, config, state, qqclient, custom, plugin):
        self.config = config
        self.state = state
        self.qqclient = qqclient
        self.custom = custom
        self.plugin = plugin

    @property
    def _root_dir(self):
        return str(self.plugin.data_folder)

    def audit_text(self, text):
        """正则 + 敏感词 + AI 二审，返回过滤后的字符串。"""
        return filter_mod.audit(text, self.config, self._root_dir)

    def send_command(self, command):
        """执行 BDS 控制台命令并捕获输出，返回 { success, output }。"""
        return self.plugin.run_on_main(lambda: self._send_command_sync(command))

    def _send_command_sync(self, command):
        output = []

        def on_message(msg):
            output.append(self._command_text(msg))

        def on_error(msg):
            output.append(self._command_text(msg))

        try:
            wrapper = CommandSenderWrapper(
                self.plugin.server.command_sender, on_message=on_message, on_error=on_error
            )
            success = self.plugin.server.dispatch_command(wrapper, str(command))
            return {"success": bool(success), "output": "\n".join(output)}
        except Exception as e:
            log.error("无法执行命令 " + str(command) + "：" + str(e))
            return {"success": False, "output": ""}

    def _command_text(self, msg):
        """把命令输出（可能是 Translatable）翻译成可读文本。"""
        try:
            if isinstance(msg, Translatable):
                return self.plugin.server.language.translate(msg)
        except Exception:
            pass
        return str(msg)

    def broadcast(self, message):
        """广播消息到游戏大厅（不取消原消息，仅转发）。"""
        self.plugin.run_on_main(lambda: self._broadcast_sync(message))

    def _broadcast_sync(self, message):
        try:
            self.plugin.server.broadcast_message(str(message))
        except Exception as e:
            log.error("广播失败：" + str(e))

    def format_game_message(self, name, message):
        fmt = self.config.get_string("chat-format.from-game", "[游戏] {name}: {message}")
        return fmt.replace("{name}", str(name)).replace("{message}", str(message))

    def format_group_message(self, name, message):
        fmt = self.config.get_string("chat-format.from-group", "[QQ] {name}: {message}")
        return fmt.replace("{name}", str(name)).replace("{message}", str(message))

    def send_to_all_groups(self, content):
        """群发：配置了 bot.groups 则发给这些群；为空 = 发给所有互动过的群。"""
        groups = self.config.get_list("bot.groups")
        if not groups:
            groups = self.state.list_groups()
        for group_id in groups:
            self.qqclient.send_group_message(group_id, content)
