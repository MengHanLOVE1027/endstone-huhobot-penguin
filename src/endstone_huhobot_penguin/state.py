"""
分群状态持久化：手动管理员 / 认证用户 / 管理方式 / 全量转发 / 白名单绑定，逐群存档。
对应 Java 版 command-state.ini。变更即写盘（tmp + rename 原子写）。
"""

import json
import os

from .logger import log

MODE_QQ = "QQ"
MODE_MANUAL = "MANUAL"
MODE_BOTH = "BOTH"


def map_config_mode(value):
    """配置值 qq/config/both → QQ/MANUAL/BOTH，对齐 Java AdminMode.from。"""
    if value == "qq":
        return MODE_QQ
    if value in ("config", "manual"):
        return MODE_MANUAL
    return MODE_BOTH


def is_valid_mode(value):
    return value in (MODE_QQ, MODE_MANUAL, MODE_BOTH)


class State:
    def __init__(self, root_dir, cfg):
        self.cfg = cfg
        self.file = os.path.join(root_dir, "command-state.json")
        self.data = {
            "administrators": {},
            "authenticated-users": {},
            "administrator-modes": {},
            "full-forwarding": {},
            "bindings": {},
        }
        self.load()

    def load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return
            for key in self.data:
                if isinstance(raw.get(key), dict):
                    self.data[key] = raw[key]
        except Exception:
            # 首次运行没有状态文件，使用空状态
            pass

    def save(self):
        tmp = self.file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, self.file)
        except Exception as e:
            log.error("状态写入失败：" + str(e))

    # ---- 手动管理员 ----

    def _manual_admins(self, group):
        if group not in self.data["administrators"]:
            self.data["administrators"][group] = []
        return self.data["administrators"][group]

    def list_admins(self, group):
        return self.data["administrators"].get(group, [])

    def contains_manual_admin(self, group, openid):
        return openid in self._manual_admins(group)

    def add_admin(self, group, openid):
        lst = self._manual_admins(group)
        if openid not in lst:
            lst.append(openid)
            self.save()

    def remove_admin(self, group, openid):
        lst = self._manual_admins(group)
        if openid in lst:
            lst.remove(openid)
            self.save()

    def is_manual_admin(self, group, openid):
        """openid 是否在 手动管理员 + admin.openids 配置 的并集中。"""
        if openid in self.cfg.get_list("admin.openids"):
            return True
        return self.contains_manual_admin(group, openid)

    # ---- 认证用户 ----

    def _authenticated(self, group):
        if group not in self.data["authenticated-users"]:
            self.data["authenticated-users"][group] = []
        return self.data["authenticated-users"][group]

    def is_authenticated(self, group, openid):
        return openid in self._authenticated(group)

    def authenticate(self, group, openid):
        lst = self._authenticated(group)
        if openid not in lst:
            lst.append(openid)
            self.save()

    def revoke(self, group, openid):
        lst = self._authenticated(group)
        if openid in lst:
            lst.remove(openid)
            self.save()

    # ---- 管理方式（每群覆盖，缺省取配置 admin.mode） ----

    def mode(self, group):
        stored = self.data["administrator-modes"].get(group)
        if stored and is_valid_mode(stored):
            return stored
        return map_config_mode(self.cfg.get_string("admin.mode", "both"))

    def set_mode(self, group, mode):
        self.data["administrator-modes"][group] = mode
        self.save()

    def is_admin(self, group, openid, role):
        """以角色 + 管理方式判定是否管理员（对齐 CommandSupport.requireAdmin）。"""
        mode = self.mode(group)
        role_ok = role in ("owner", "admin")
        manual_ok = self.is_manual_admin(group, openid)
        if mode == MODE_QQ:
            return role_ok
        if mode == MODE_MANUAL:
            return manual_ok
        return role_ok or manual_ok

    # ---- 全量转发（每群覆盖，缺省取配置 features.full-amount） ----

    def is_full_forwarding(self, group):
        value = self.data["full-forwarding"].get(group)
        if value is not None:
            return bool(value)
        return self.cfg.get_bool("features.full-amount", False)

    def set_full_forwarding(self, group, enabled):
        self.data["full-forwarding"][group] = bool(enabled)
        self.save()

    # ---- 白名单绑定（QQ OpenID ⇄ 游戏名）：自助绑定 / 管理员解绑 ----

    def _bindings(self, group):
        if group not in self.data["bindings"] or not isinstance(self.data["bindings"][group], dict):
            self.data["bindings"][group] = {}
        return self.data["bindings"][group]

    def get_binding_name(self, group, openid):
        group_map = self.data["bindings"].get(group)
        if not group_map:
            return None
        value = group_map.get(openid)
        return str(value) if value else None

    def bind_name(self, group, openid, game_name):
        self._bindings(group)[openid] = str(game_name)
        self.save()
        return str(game_name)

    def unbind_openid(self, group, openid):
        group_map = self.data["bindings"].get(group)
        if not group_map or not group_map.get(openid):
            return None
        game_name = str(group_map[openid])
        del group_map[openid]
        self.save()
        return game_name

    def find_openid_by_game_name(self, group, game_name):
        group_map = self.data["bindings"].get(group)
        if not group_map:
            return None
        key = str(game_name).lower()
        for openid, stored in group_map.items():
            if str(stored).lower() == key:
                return openid
        return None
