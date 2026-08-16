"""
命令分发 + 全部内置命令，对齐 commands.js（Java 版 BaseCommand/CommandSupport/
PublicCommands/AdministrationCommands/AuthenticationCommands）。

handle_group_message 入口：
  1. 群白名单（bot.groups 为空 = 所有群）
  2. 剥 @ 前缀/前导 /，按命令名长度降序匹配（cleaned==name 或 startsWith(name+" ")）
  3. 命中 → 检查命令开关 → 执行
  4. 非命令 → 全量转发（full-forwarding 且 post-chat 时广播到游戏）
"""

import re
from urllib.parse import quote

from .custom_commands import render_command
from .logger import log
from .qqclient import request_json
from .state import MODE_QQ, MODE_MANUAL, MODE_BOTH

MODE_LABEL = {
    MODE_QQ: "QQ 群主/管理员",
    MODE_MANUAL: "手动管理员",
    MODE_BOTH: "双重（两者任一即可）",
}


# ---- 通用小工具 ----

def normalize_content(content):
    cleaned = str(content or "").strip()
    # 官方 GROUP_AT 事件已剥离 @ 前缀；若仍带 <@xxx> 则兜底去掉
    cleaned = re.sub(r"<@!?[^>]+>", "", cleaned).strip()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:].strip()
    return cleaned


def parse_player_list(output):
    """解析 BDS `list` 输出的在线玩家名列表；无法识别返回 None（回退纯文本）。"""
    text = str(output or "").strip()
    if not text:
        return None
    m = re.search(r"[:：][^:：]*$", text)
    if not m:
        return None
    names_part = m.group(0)[1:].strip()
    if not names_part:
        return []
    if re.search(r"[。！？!?]", names_part) or re.search(r"在线|online|players|玩家", names_part, re.IGNORECASE):
        return None
    names = []
    for s in re.split(r"[,，、]", names_part):
        s = s.strip()
        if (s and s != "无"
                and not re.search(r"no players|无玩家|0\s*位|0\s*个|0\s*人", s, re.IGNORECASE)
                and not re.search(r"^[-=]+$", s)):
            names.append(s)
    return names


def build_online_markdown(bot, players):
    lines = ["# 在线玩家", ""]
    motd_ip = str(bot.config.get_string("motd.ip", "") or "").strip()
    if motd_ip:
        port = bot.config.get_int("motd.port", 19132)
        qs = "ip=" + quote(motd_ip) + "&port=" + str(port)
        lines.append("![服务器状态 #480px #270px](https://motd.minebbs.com/api/status_img?" + qs + ")")
        lines.append("")
    lines.append("当前在线：**" + str(len(players)) + "** 人")
    if players:
        lines.append("")
        for p in players:
            lines.append("- " + p)
    return "\n".join(lines)


def parse_whitelist(output):
    """解析 BDS `allowlist list` / `whitelist list` 输出的玩家名列表；无法识别返回 None。"""
    text = str(output or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = __import__("json").loads(m.group(0))
            if isinstance(data, dict) and isinstance(data.get("result"), list):
                names = []
                for r in data["result"]:
                    if isinstance(r, dict) and str(r.get("name") or "").strip():
                        names.append(str(r["name"]).strip())
                return names
        except Exception:
            pass  # 非 JSON，继续走文本解析
    m2 = re.search(r"[:：][^:：]*$", text)
    if m2:
        names_part = m2.group(0)[1:].strip()
        if not names_part:
            return []
        names = []
        for s in re.split(r"[,，、]", names_part):
            s = s.strip()
            if (s and s != "无"
                    and not re.search(r"no players|无玩家|0\s*位|0\s*个|0\s*人", s, re.IGNORECASE)):
                names.append(s)
        return names
    return None


def build_whitelist_markdown(bot, names):
    lines = ["# 白名单", "", "当前白名单：**" + str(len(names)) + "** 人"]
    if names:
        lines.append("")
        for n in names:
            lines.append("- " + n)
    return "\n".join(lines)


def strip_motd_colors(text):
    return re.sub(r"§.", "", str(text or "")).strip()


def build_server_query_markdown(data, ip, port):
    lines = ["# " + (data.get("host") or (ip + ":" + str(port))), ""]
    lines.append("![服务器状态 #480px #270px](https://motd.minebbs.com/api/status_img?ip=" +
                 quote(ip) + "&port=" + str(port) + ")")
    lines.append("")
    lines.append("状态：**在线**" + ("（" + str(data["type"]) + "）" if data.get("type") else ""))
    p = data.get("players") or {}
    lines.append("玩家：**" + str(p.get("online")) + " / " + str(p.get("max")) + "**")
    if data.get("version"):
        lines.append("版本：" + str(data["version"]))
    if data.get("delay") is not None:
        lines.append("延迟：" + str(data["delay"]) + "ms")
    motd = strip_motd_colors(data.get("pureMotd") or data.get("motd"))
    if motd:
        lines.append("")
        for ln in [x for x in motd.split("\n") if x]:
            lines.append("- " + ln)
    return "\n".join(lines)


def format_server_query(data, ip, port):
    if not data or data.get("status") != "online":
        host = data.get("host") if data else None
        return (host or (ip + ":" + str(port))) + " 离线" + (("：" + str(data.get("error"))) if data and data.get("error") else "")
    p = data.get("players") or {}
    motd = strip_motd_colors(data.get("pureMotd") or data.get("motd"))
    lines = [
        data.get("host") or (ip + ":" + str(port)),
        "状态：在线（" + str(data.get("type") or "未知类型") + "）",
        "玩家：" + str(p.get("online")) + " / " + str(p.get("max")),
    ]
    if data.get("version"):
        lines.append("版本：" + str(data["version"]))
    if data.get("delay") is not None:
        lines.append("延迟：" + str(data["delay"]) + "ms")
    if motd:
        lines.append("MOTD：" + motd.replace("\n", " "))
    return "\n".join(lines)


def parse_on_off(text):
    t = str(text or "").strip().lower()
    if t in ("开", "on", "true", "1", "yes"):
        return True
    if t in ("关", "off", "false", "0", "no"):
        return False
    return None


def parse_mode(text):
    t = str(text or "").strip().lower()
    if t == "qq":
        return MODE_QQ
    if t in ("手动", "manual", "config"):
        return MODE_MANUAL
    if t in ("双重", "both"):
        return MODE_BOTH
    return None


def reply(ctx, content):
    ctx["bot"].qqclient.send_group_message(ctx["groupId"], content, ctx["msgId"])


def require_admin(ctx):
    return ctx["bot"].state.is_admin(ctx["groupId"], ctx["userId"], ctx["memberRole"])


def gate_admin(ctx, usage=None):
    if require_admin(ctx):
        return True
    reply(ctx, "此命令需要管理员权限")
    if usage:
        log.debug("用法：" + usage)
    return False


def run_game_command(bot, ctx, command):
    result = bot.send_command(command)
    text = str((result or {}).get("output") or "").strip()
    reply(ctx, text or "已发送执行请求")


def display_name(ctx):
    return ctx.get("username") or ctx.get("userId") or "未知"


# ---- 命令定义 ----

def _cmd_chaxinxi(ctx):
    target = str(ctx["params"] or "").strip()
    if not target:
        reply(ctx, "群：" + ctx["groupId"] +
              "\n本人 OpenID：" + ctx["userId"] +
              "\n角色：" + (ctx["memberRole"] or "member") +
              "\n认证状态：" + ("已认证" if ctx["bot"].state.is_authenticated(ctx["groupId"], ctx["userId"]) else "未认证"))
        return
    if not gate_admin(ctx):
        return
    openid = re.split(r"\s+", target)[0]
    reply(ctx, "目标 OpenID：" + openid +
          "\n认证状态：" + ("已认证" if ctx["bot"].state.is_authenticated(ctx["groupId"], openid) else "未认证"))


def _cmd_famessage(ctx):
    if not str(ctx["params"] or "").strip():
        reply(ctx, "用法：发消息 <内容>")
        return
    filtered = ctx["bot"].audit_text(ctx["params"])
    ctx["bot"].broadcast(ctx["bot"].format_group_message(display_name(ctx), filtered))


def _cmd_faxinxi(ctx):
    if not str(ctx["params"] or "").strip():
        reply(ctx, "用法：发信息 <内容>")
        return
    filtered = ctx["bot"].audit_text(ctx["params"])
    ctx["bot"].broadcast(ctx["bot"].format_group_message(display_name(ctx), filtered))


def _cmd_chazaixian(ctx):
    result = ctx["bot"].send_command("list")
    output = str((result or {}).get("output") or "").strip()
    if not output:
        reply(ctx, "无输出")
        return
    if (ctx["bot"].config.get_bool("features.markdown-query-online", True)
            and hasattr(ctx["bot"].qqclient, "send_markdown")):
        players = parse_player_list(output)
        if players is not None:
            ctx["bot"].qqclient.send_markdown(ctx["groupId"], build_online_markdown(ctx["bot"], players), ctx["msgId"])
            return
        log.info("查在线 Markdown 解析失败，回退纯文本。list 原始输出：" + repr(output))
    reply(ctx, output)


def _cmd_zaixianfuwuqi(ctx):
    reply(ctx, ctx["bot"].config.get_string("bot.name", "HuHoBot") + " 在线")


def _cmd_motd(ctx):
    target = str(ctx["params"] or "").strip()
    if not target:
        reply(ctx, "用法：motd <地址[:端口]>（如 motd play.example.com:19132，不带端口默认 19132）")
        return
    ip = target
    port = 19132
    m = re.match(r"^(.*):(\d{1,5})$", target)
    if m:
        ip = m.group(1).strip()
        port = int(m.group(2))
    try:
        data = request_json("motd.minebbs.com", "/api/status?ip=" + quote(ip) + "&port=" + str(port),
                            method="GET", label="motd status")
    except Exception as e:
        ctx["bot"].qqclient.send_group_message(ctx["groupId"], "查询失败：" + str(e), ctx["msgId"])
        return
    online = bool(data and data.get("status") == "online")
    if online:
        ctx["bot"].qqclient.send_markdown(ctx["groupId"], build_server_query_markdown(data, ip, port), ctx["msgId"])
    else:
        ctx["bot"].qqclient.send_group_message(ctx["groupId"], format_server_query(data, ip, port), ctx["msgId"])


def _cmd_zhixing(ctx):
    item = ctx["bot"].custom.resolve_run(ctx["params"])
    if not item:
        reply(ctx, "未找到可执行的自定义命令：" + str(ctx["params"]) + (require_admin(ctx) and "（若需权限请使用 管理员执行）" or ""))
        return
    command = render_command(item["command"], ctx["params"], ctx["groupId"], ctx["userId"], "huhobot run")
    run_game_command(ctx["bot"], ctx, command)


def _cmd_zhixingmingling(ctx):
    if not gate_admin(ctx):
        return
    command = str(ctx["params"] or "").strip()
    if not command:
        reply(ctx, "用法：执行命令 <控制台命令>")
        return
    run_game_command(ctx["bot"], ctx, command)


def _cmd_guanliyuanzhixing(ctx):
    if not gate_admin(ctx):
        return
    item = ctx["bot"].custom.resolve_admin_run(ctx["params"])
    if not item:
        reply(ctx, "未找到自定义命令：" + str(ctx["params"]))
        return
    command = render_command(item["command"], ctx["params"], ctx["groupId"], ctx["userId"], "huhobot adminrun")
    run_game_command(ctx["bot"], ctx, command)


def _cmd_chaguanli(ctx):
    admins = ctx["bot"].state.list_admins(ctx["groupId"])
    configured = ctx["bot"].config.get_list("admin.openids")
    lines = ["手动管理员：" + ("、".join(admins) if admins else "无")]
    if configured:
        lines.append("配置的管理员：" + "、".join(configured))
    reply(ctx, "\n".join(lines))


def _cmd_jiaguanli(ctx):
    if not gate_admin(ctx):
        return
    target = str(ctx["params"] or "").strip()
    if not target:
        reply(ctx, "用法：加管理 <OpenID>")
        return
    openid = re.split(r"\s+", target)[0]
    ctx["bot"].state.add_admin(ctx["groupId"], openid)
    reply(ctx, "已添加管理员：" + openid)


def _cmd_shanguanli(ctx):
    if not gate_admin(ctx):
        return
    target = str(ctx["params"] or "").strip()
    if not target:
        reply(ctx, "用法：删管理 <OpenID>")
        return
    openid = re.split(r"\s+", target)[0]
    ctx["bot"].state.remove_admin(ctx["groupId"], openid)
    reply(ctx, "已删除管理员：" + openid)


def _cmd_guanlifangshi(ctx):
    if not gate_admin(ctx):
        return
    mode = parse_mode(ctx["params"])
    if not mode:
        reply(ctx, "用法：管理方式 <QQ/手动/双重>")
        return
    ctx["bot"].state.set_mode(ctx["groupId"], mode)
    reply(ctx, "已设置本群管理方式：" + MODE_LABEL[mode])


def _cmd_tianjiabaimingdan(ctx):
    if not gate_admin(ctx):
        return
    name = str(ctx["params"] or "").strip()
    if not name:
        reply(ctx, "用法：添加白名单 <玩家名>")
        return
    command = ctx["bot"].config.get_string("whitelist.add-command", "whitelist add {name}").replace("{name}", name)
    run_game_command(ctx["bot"], ctx, command)


def _cmd_shanchubaimingdan(ctx):
    if not gate_admin(ctx):
        return
    name = str(ctx["params"] or "").strip()
    if not name:
        reply(ctx, "用法：删除白名单 <玩家名>")
        return
    command = ctx["bot"].config.get_string("whitelist.del-command", "whitelist remove {name}").replace("{name}", name)
    run_game_command(ctx["bot"], ctx, command)


def _cmd_chabaimingdan(ctx):
    result = ctx["bot"].send_command("allowlist list")
    output = str((result or {}).get("output") or "").strip()
    if not output:
        reply(ctx, "无输出")
        return
    if (ctx["bot"].config.get_bool("features.markdown-whitelist", True)
            and hasattr(ctx["bot"].qqclient, "send_markdown")):
        names = parse_whitelist(output)
        if names is not None:
            ctx["bot"].qqclient.send_markdown(ctx["groupId"], build_whitelist_markdown(ctx["bot"], names), ctx["msgId"])
            return
        log.info("查白名单 Markdown 解析失败，回退纯文本。原始输出：" + repr(output))
    reply(ctx, output)


def _cmd_bangdingbaimingdan(ctx):
    name = str(ctx["params"] or "").strip()
    if not name:
        reply(ctx, "用法：绑定白名单 <玩家名>")
        return
    openid = ctx["userId"]
    ctx["bot"].state.bind_name(ctx["groupId"], openid, name)
    command = ctx["bot"].config.get_string("whitelist.add-command", "whitelist add {name}").replace("{name}", name)
    ctx["bot"].send_command(command)
    reply(ctx, "绑定成功：QQ " + openid + " ⇄ 游戏 " + name + "\n已执行白名单加入：" + command)


def _cmd_jiebangbaimingdan(ctx):
    if not gate_admin(ctx):
        return
    name = str(ctx["params"] or "").strip()
    if not name:
        reply(ctx, "用法：解绑白名单 <玩家名>（撤销该游戏名对应的 QQ 绑定并移出白名单）")
        return
    openid = ctx["bot"].state.find_openid_by_game_name(ctx["groupId"], name)
    removed = None
    if openid:
        removed = ctx["bot"].state.unbind_openid(ctx["groupId"], openid)
    command = ctx["bot"].config.get_string("whitelist.del-command", "whitelist remove {name}").replace("{name}", name)
    ctx["bot"].send_command(command)
    if openid:
        reply(ctx, "已解绑：QQ " + openid + " ⇄ 游戏 " + removed + "\n已执行白名单移除：" + command)
    else:
        reply(ctx, "未找到该游戏名的绑定记录（已执行白名单移除：" + command + "）")


def _cmd_jiechubangding(ctx):
    openid = ctx["userId"]
    removed = ctx["bot"].state.unbind_openid(ctx["groupId"], openid)
    if not removed:
        reply(ctx, "你当前没有绑定记录")
        return
    command = ctx["bot"].config.get_string("whitelist.del-command", "whitelist remove {name}").replace("{name}", removed)
    ctx["bot"].send_command(command)
    reply(ctx, "已解除绑定：QQ " + openid + " ⇄ 游戏 " + removed + "\n已执行白名单移除：" + command)


def _cmd_renzheng(ctx):
    target = str(ctx["params"] or "").strip()
    if not target:
        status = "已认证" if ctx["bot"].state.is_authenticated(ctx["groupId"], ctx["userId"]) else "未认证"
        reply(ctx, "本人认证状态：" + status + "\nOpenID：" + ctx["userId"])
        return
    if not gate_admin(ctx):
        return
    openid = re.split(r"\s+", target)[-1]
    ctx["bot"].state.authenticate(ctx["groupId"], openid)
    reply(ctx, "已认证：" + openid)


def _cmd_jiechurenzheng(ctx):
    target = str(ctx["params"] or "").strip()
    if not target:
        ctx["bot"].state.revoke(ctx["groupId"], ctx["userId"])
        reply(ctx, "已解除本人认证")
        return
    if not gate_admin(ctx):
        return
    openid = re.split(r"\s+", target)[-1]
    ctx["bot"].state.revoke(ctx["groupId"], openid)
    reply(ctx, "已解除认证：" + openid)


def _cmd_quanliang(ctx):
    if not gate_admin(ctx):
        return
    enabled = parse_on_off(ctx["params"])
    if enabled is None:
        reply(ctx, "用法：全量 <开/关>")
        return
    ctx["bot"].state.set_full_forwarding(ctx["groupId"], enabled)
    reply(ctx, "已设置本群全量转发：" + ("开" if enabled else "关"))


COMMANDS = [
    {"name": "查信息", "execute": _cmd_chaxinxi},
    {"name": "发消息", "execute": _cmd_famessage},
    {"name": "发信息", "execute": _cmd_faxinxi},
    {"name": "查在线", "execute": _cmd_chazaixian},
    {"name": "在线服务器", "execute": _cmd_zaixianfuwuqi},
    {"name": "motd", "execute": _cmd_motd},
    {"name": "执行", "execute": _cmd_zhixing},
    {"name": "执行命令", "execute": _cmd_zhixingmingling},
    {"name": "管理员执行", "execute": _cmd_guanliyuanzhixing},
    {"name": "查管理", "execute": _cmd_chaguanli},
    {"name": "加管理", "execute": _cmd_jiaguanli},
    {"name": "删管理", "execute": _cmd_shanguanli},
    {"name": "管理方式", "execute": _cmd_guanlifangshi},
    {"name": "添加白名单", "execute": _cmd_tianjiabaimingdan},
    {"name": "删除白名单", "execute": _cmd_shanchubaimingdan},
    {"name": "查白名单", "execute": _cmd_chabaimingdan},
    {"name": "绑定白名单", "execute": _cmd_bangdingbaimingdan},
    {"name": "解绑白名单", "execute": _cmd_jiebangbaimingdan},
    {"name": "解除绑定", "execute": _cmd_jiechubangding},
    {"name": "认证", "execute": _cmd_renzheng},
    {"name": "解除认证", "execute": _cmd_jiechurenzheng},
    {"name": "全量", "execute": _cmd_quanliang},
]

COMMAND_NAMES = [cmd["name"] for cmd in COMMANDS]

_ORDERED = sorted(COMMANDS, key=lambda c: len(c["name"]), reverse=True)


def dispatch(cleaned):
    for cmd in _ORDERED:
        if cleaned == cmd["name"] or cleaned.startswith(cmd["name"] + " "):
            params = "" if cleaned == cmd["name"] else cleaned[len(cmd["name"]):].strip()
            return {"cmd": cmd, "params": params}
    return None


def handle_group_message(bot, message):
    """群消息总入口。message: { id, groupId, content, userId, username, memberRole, timestamp }"""
    if not message or not message.get("groupId") or message.get("content") is None:
        return False

    log_events = bot.config.get_bool("debug.log-events", False)
    groups = bot.config.get_list("bot.groups")
    if groups and message["groupId"] not in groups:
        if log_events:
            log.info("群 " + str(message["groupId"]) + " 不在 bot.groups 白名单，忽略")
        return False

    ctx = {
        "bot": bot,
        "msgId": message.get("id"),
        "groupId": message["groupId"],
        "userId": message.get("userId"),
        "username": message.get("username"),
        "memberRole": message.get("memberRole"),
    }

    cleaned = normalize_content(message["content"])
    if cleaned:
        match = dispatch(cleaned)
        if match:
            toggle_key = "commands." + match["cmd"]["name"]
            if not bot.config.get_bool(toggle_key, True):
                if log_events:
                    log.info("命令 " + match["cmd"]["name"] + " 已被关闭，忽略")
                reply(ctx, "此命令已被管理员关闭")
                return True
            if log_events:
                log.info("命中命令：" + match["cmd"]["name"] + " 参数=" + repr(match["params"]))
            ctx["params"] = match["params"]
            try:
                match["cmd"]["execute"](ctx)
            except Exception:
                log.exception("命令 " + match["cmd"]["name"] + " 执行出错")
            return True
        if log_events:
            log.info("非命令消息（bot.groups=" + repr(groups) +
                     "，isFullForwarding=" + str(bot.state.is_full_forwarding(message["groupId"])) + "）")

    # 非命令消息：全量转发到游戏
    if bot.config.get_bool("chat-format.post-chat", True) and bot.state.is_full_forwarding(message["groupId"]):
        raw = str(message["content"] or "").strip()
        if raw:
            filtered = bot.audit_text(raw)
            bot.broadcast(bot.format_group_message(display_name(ctx), filtered))
            return True
    return False
