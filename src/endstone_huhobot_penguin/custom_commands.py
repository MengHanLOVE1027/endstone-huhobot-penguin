"""
自定义命令：对齐 Java CustomCommandRegistry。
配置形如 { "key": "某某", "command": "say {params}", "permission": 0 }
permission > 0 的命令只能由管理员执行（管理员执行）。
占位符：{params} 全部参数、{group} 群 OpenID、{user} 用户 OpenID、
        {0}/{1}... 第 N 个参数、&0/&1... 同义。
"""

from .logger import log


def render_command(command, params, group_id, user_id, prefix=""):
    tokens = [t for t in str(params or "").strip().split() if t]
    out = str(command)
    out = out.replace("{params}", str(params or ""))
    out = out.replace("{group}", str(group_id or ""))
    out = out.replace("{user}", str(user_id or ""))
    if prefix:
        out = out.replace("{prefix}", prefix)
    for index, token in enumerate(tokens):
        out = out.replace("{" + str(index) + "}", token)
        out = out.replace("&" + str(index), token)
    return out


class CustomCommands:
    def __init__(self, cfg):
        self.cfg = cfg
        self.list = []
        self.reload()

    def reload(self):
        self.list = []
        for item in self.cfg.get_list("custom-commands"):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "") or "").strip()
            command = str(item.get("command", "") or "").strip()
            permission = item.get("permission", 0)
            try:
                permission = int(permission)
            except (TypeError, ValueError):
                permission = 0
            if not key or not command:
                log.warning("[HuHoBotPenguin] 忽略缺少 key 或 command 的自定义命令配置")
                continue
            self.list.append({"key": key, "command": command, "permission": permission})

    def find(self, key):
        key = str(key or "").strip()
        for item in self.list:
            if item["key"] == key:
                return item
        return None

    def resolve_run(self, key):
        """执行：仅权限为 0 的命令可被普通成员使用。"""
        item = self.find(key)
        if not item or item["permission"] > 0:
            return None
        return item

    def resolve_admin_run(self, key):
        """管理员执行：任意权限。"""
        return self.find(key)
