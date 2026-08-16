"""
配置加载：读取插件目录下 config.json，缺省补全 + config-version 版本升级。
键名与原 Java 版 config.yml 保持一致（点号分层），读取方式对齐 Java ConfigManager。
"""

import json
import os

from .logger import log

CONFIG_VERSION = 2

COMMAND_NAMES = [
    "查信息",
    "查管理",
    "加管理",
    "删管理",
    "管理方式",
    "添加白名单",
    "删除白名单",
    "查白名单",
    "查在线",
    "在线服务器",
    "motd",
    "发信息",
    "发消息",
    "执行命令",
    "执行",
    "管理员执行",
    "全量",
    "认证",
    "解除认证",
    "绑定白名单",
    "解绑白名单",
    "解除绑定",
]

DEFAULT_VALUES = {
    "config-version": CONFIG_VERSION,
    "bot.app-id": "",
    "bot.secret": "",
    "bot.name": "HuHoBot",
    "bot.groups": [],
    "serverName": "",

    "chat-format.from-game": "[游戏] {name}: {message}",
    "chat-format.from-group": "[QQ] {name}: {message}",
    "chat-format.post-chat": True,
    "chat-format.start-with": "",

    "whitelist.add-command": "whitelist add {name}",
    "whitelist.del-command": "whitelist remove {name}",

    "filter-regex": [],
    "admin.mode": "both",
    "admin.openids": [],

    "features.full-amount": False,
    "features.markdown-query-online": True,
    "features.markdown-whitelist": True,

    "motd.ip": "",
    "motd.port": 19132,

    "join-leave.enabled": True,
    "join-leave.join-format": "[{server}] 🟢{name}进入服务器",
    "join-leave.leave-format": "[{server}] 🔴{name}退出服务器",

    "audit.base-url": "",
    "audit.api-key": "",
    "audit.model": "gpt-4o-mini",

    "custom-commands": [],
    "debug.probe": False,
    "debug.log-events": False,
}

for _name in COMMAND_NAMES:
    DEFAULT_VALUES["commands." + _name] = True


def _deep_clone(value):
    return json.loads(json.dumps(value))


def flatten(value, prefix, out):
    """嵌套 JSON 展平为点号键映射（bot.app-id 风格）。"""
    for key, item in value.items():
        path = prefix + "." + key if prefix else key
        if isinstance(item, dict):
            flatten(item, path, out)
        else:
            out[path] = item
    return out


def nest(flat):
    """点号键映射还原为嵌套 JSON。"""
    root = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = root
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return root


def _migrate_post_prefix(flat):
    """旧版 chat-format.post-prefix 迁移到 chat-format.start-with。"""
    if "chat-format.post-prefix" not in flat:
        return False
    if "chat-format.start-with" not in flat:
        flat["chat-format.start-with"] = flat["chat-format.post-prefix"]
    flat.pop("chat-format.post-prefix", None)
    return True


def _fill_missing(flat):
    changed = False
    for key, value in DEFAULT_VALUES.items():
        if key not in flat:
            flat[key] = _deep_clone(value)
            changed = True
    return changed


class Config:
    """强类型读取门面，对齐 Java ConfigProvider 的用法。"""

    def __init__(self, raw):
        self.raw = raw

    def get_string(self, key, default):
        value = self.raw.get(key)
        return default if value is None else str(value)

    def get_int(self, key, default):
        value = self.raw.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key, default):
        value = self.raw.get(key)
        return default if value is None else bool(value)

    def get_list(self, key):
        value = self.raw.get(key)
        return value if isinstance(value, list) else []

    def get_section(self, key):
        value = self.raw.get(key)
        return value if isinstance(value, dict) else {}


def load(root_dir):
    """读取 config.json，补全缺省并写回，返回 Config 门面。"""
    config_path = os.path.join(root_dir, "config.json")

    nested = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            nested = json.load(f)
            if not isinstance(nested, dict):
                nested = {}
    except Exception as e:
        log.warning("[HuHoBotPenguin] config.json 读取失败，使用默认配置：" + str(e))

    flat = flatten(nested, "", {})
    changed = _migrate_post_prefix(flat)
    changed = _fill_missing(flat) or changed

    previous_version = flat.get("config-version", 0)
    if not isinstance(previous_version, int):
        try:
            previous_version = int(previous_version)
        except (TypeError, ValueError):
            previous_version = 0
    if previous_version != CONFIG_VERSION:
        flat["config-version"] = CONFIG_VERSION
        changed = True

    if changed:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(nest(flat), f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            log.warning("[HuHoBotPenguin] 配置写入失败：" + str(e))
        log.info("[HuHoBotPenguin] 配置文件已升级到版本 " + str(CONFIG_VERSION) + "（旧版本：" + str(previous_version) + "）")

    return Config(flat)
