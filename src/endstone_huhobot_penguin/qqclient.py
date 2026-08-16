"""
QQ 开放平台 WebSocket 网关客户端（对齐 qqclient.js）。
- access_token：POST bots.qq.com/app/getAppAccessToken（7200s 内提前 60s 刷新，刷新串行化）
- 网关地址：GET api.bot.qq.com/gateway
- Identify(op2)：intents = 1<<25（GROUP_AND_C2C_EVENT）
- 群消息：GROUP_AT_MESSAGE_CREATE + GROUP_MESSAGE_CREATE
- 心跳(op1)/断线重连；优先 Resume(op6)，失败/服务端要求再全新 Identify
- 发消息：POST api.bot.qq.com/v2/groups/{group_openid}/messages，串行队列 + 节流防限频
"""

import json
import platform
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .logger import log
from .wsc import WSC, OP_TEXT

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

INTENTS_GROUP_AND_C2C = 1 << 25
TOKEN_REFRESH_LEAD = 60  # 秒，提前刷新
MAX_RECONNECT_DELAY = 30  # 秒
SEND_GAP_MS = 0.5  # 发消息串行队列的节流间隔


def request_json(host, path, method="GET", headers=None, body=None, label=None, timeout=15):
    """HTTP(S) JSON 请求（urllib 实现），失败抛 RuntimeError，成功返回解析后的对象。"""
    label = label or path
    url = "https://" + host + path
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(label + " HTTP " + str(e.code) + "：" + detail)
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(label + " 请求失败：" + str(e))
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise RuntimeError(label + " 响应非 JSON：" + raw[:200])
    # token 接口失败也可能以 HTTP 200 返回 {code:!, message}，此处兜底
    if isinstance(parsed, dict) and parsed.get("code") is not None and parsed.get("code") != 0:
        raise RuntimeError(label + " 返回错误码 " + str(parsed.get("code")) + "：" +
                           str(parsed.get("message") or raw[:200]))
    return parsed


class QQClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.app_id = cfg.get_string("bot.app-id", "")
        self.secret = cfg.get_string("bot.secret", "")
        self.bot_name = cfg.get_string("bot.name", "HuHoBot")
        # 发布版固定使用正式环境（机器人需提审上线后才收得到群事件）
        self.backend_host = "api.bot.qq.com"

        self.access_token = None
        self.token_expire_at = 0
        self._token_lock = threading.Lock()

        self.ws = None
        self.connected = False
        self.ready = False
        self.last_seq = None
        self.session_id = None
        self.first_connect = True

        self.heartbeat_interval = 41250  # ms
        self._last_ack = time.time()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

        self.reconnect_attempt = 0
        self.recent_ids = []  # 去重，保留最近 200 条
        self._send_queue = queue.Queue()
        self._sender_thread = None

        self.stopped = False
        self._stop_event = threading.Event()
        self._run_thread = None

        # 回调：收到群消息 { id, groupId, content, userId, username, memberRole, timestamp }
        self.on_group_message = None

    # ---- 生命周期 ----

    def start(self):
        self.stopped = False
        self.first_connect = True
        self._stop_event.clear()
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        self._run_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._run_thread.start()

    def stop(self):
        self.stopped = True
        self._stop_event.set()
        self._heartbeat_stop.set()
        if self.ws:
            try:
                self.ws.close(1000, "shutdown")
            except Exception:
                pass
            self.ws = None
        self.connected = False
        self.ready = False
        # 解除 sender 阻塞
        try:
            self._send_queue.put_nowait(None)
        except Exception:
            pass

    # ---- 连接主循环 ----

    def _run_loop(self):
        while not self.stopped:
            try:
                self._connect_once()
            except Exception as e:
                log.error("连接失败：" + str(e))
            if self.stopped:
                break
            backoff = min(MAX_RECONNECT_DELAY, 2 ** self.reconnect_attempt)
            self.reconnect_attempt += 1
            wait = backoff + random.uniform(0, 0.5)
            log.info("" + str(round(wait, 2)) + "s 后重连网关…")
            if self._stop_event.wait(wait):
                break

    def _connect_once(self):
        token = self.get_access_token()
        log.info("环境：正式，后端 " + self.backend_host + "。机器人需提审上线后才能在正式环境收到群事件")
        url = self._get_gateway_url(token)
        if self.stopped:
            return
        log.info("网关地址：" + url)

        ws = WSC(url, headers={
            "Authorization": "QQBot " + (token or ""),
            "X-Union-Appid": self.app_id,
        }, connect_timeout=15)
        self.ws = ws
        self.connected = False
        self.ready = False
        ws.connect()
        self.connected = True

        self._send_identify_or_resume(ws, token)

        try:
            while not self.stopped and ws.is_open():
                frame = ws.recv_frame()
                if frame is None:
                    break
                opcode, payload = frame
                if opcode != OP_TEXT:
                    continue
                self._handle_message(ws, payload)
        finally:
            self._stop_heartbeat()
            if self.ws is ws:
                self.ws = None
            try:
                ws.close(1000, "closed")
            except Exception:
                pass
            self.connected = False
            self.ready = False
            if not self.stopped:
                log.warning("网关连接断开，准备重连")

    def _send_identify_or_resume(self, ws, token):
        if self.session_id and not self.first_connect:
            log.info("尝试 Resume 已断开会话…")
            ws.send_text(json.dumps({
                "op": OP_RESUME,
                "d": {"token": "QQBot " + token, "session_id": self.session_id, "seq": self.last_seq},
            }))
        else:
            os_name = (platform.system() or "linux").lower()
            ws.send_text(json.dumps({
                "op": OP_IDENTIFY,
                "d": {
                    "token": "QQBot " + token,
                    "intents": INTENTS_GROUP_AND_C2C,
                    "shard": [0, 1],
                    "properties": {
                        "$os": os_name,
                        "$browser": self.bot_name + " (EndStone)",
                        "$device": self.bot_name + " (EndStone)",
                    },
                },
            }))

    # ---- 网关帧处理 ----

    def _handle_message(self, ws, data):
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("收到非法 JSON 帧：" + data[:200].decode("utf-8", "replace"))
            return
        op = payload.get("op")
        if op == OP_HELLO:
            self._on_hello(ws, payload.get("d") or {})
        elif op == OP_HEARTBEAT_ACK:
            self._last_ack = time.time()
        elif op == OP_INVALID_SESSION:
            self._on_invalid_session(ws, payload.get("d"))
        elif op == OP_DISPATCH:
            self._on_dispatch(payload)
        elif op == OP_RECONNECT:
            log.warning("服务端要求重连（op7）")
            ws.close(4000, "server-reconnect")
        else:
            log.debug("未处理 op=" + str(op))

    def _on_hello(self, ws, hello):
        interval = hello.get("heartbeat_interval") or 41250
        self.heartbeat_interval = interval
        self._last_ack = time.time()
        self._start_heartbeat(ws)

    def _start_heartbeat(self, ws):
        self._stop_heartbeat()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self):
        self._heartbeat_stop.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2)
        self._heartbeat_thread = None

    def _heartbeat_loop(self, ws):
        stop = self._heartbeat_stop
        interval = max(1000, self.heartbeat_interval or 41250) / 1000.0
        while not stop.is_set():
            if stop.wait(interval):
                break
            if self.ws is not ws or not ws.is_open() or not self.connected:
                break
            try:
                ws.send_text(json.dumps({"op": OP_HEARTBEAT, "d": self.last_seq}))
            except Exception:
                break
            if time.time() - self._last_ack > interval * 2:
                log.warning("心跳 ACK 超时，主动断开重连")
                try:
                    ws.close(1000, "heartbeat-timeout")
                except Exception:
                    pass
                break

    def _on_invalid_session(self, ws, d):
        log.warning("收到 Invalid Session（d=" + str(d) + "），重建会话")
        if d is True:
            # 服务端作废旧会话，必须全新 Identify
            self.session_id = None
        # d === false：重连后可再尝试 Resume
        try:
            ws.close(1000, "invalid-session")
        except Exception:
            pass

    def _on_dispatch(self, payload):
        self.last_seq = payload.get("s")
        t = payload.get("t")
        if not t:
            return
        if self.cfg.get_bool("debug.log-events", False):
            d = payload.get("d") or {}
            type_part = (" type=" + str(d.get("type"))) if "type" in d else ""
            log.info("收到 Dispatch：t=" + str(t) + type_part +
                     " 前200字符=" + json.dumps(payload)[:200])
        if t == "READY":
            d = payload.get("d") or {}
            self.session_id = d.get("session_id")
            self.ready = True
            self.first_connect = False
            self.reconnect_attempt = 0
            log.info("QQ 机器人已连接（session_id=" + str(self.session_id) + "）")
            return
        if t == "RESUMED":
            self.ready = True
            self.first_connect = False
            log.info("Resume 成功，会话已恢复")
            return
        if t in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
            self._on_group_message(payload.get("d") or {})

    def _on_group_message(self, d):
        if not d.get("id") or not d.get("group_openid"):
            log.warning("群消息事件缺少 group_openid/id 字段：" + json.dumps(d)[:300])
            return
        # 官方可能重复推送相同 msg_id
        if d["id"] in self.recent_ids:
            return
        self.recent_ids.append(d["id"])
        if len(self.recent_ids) > 200:
            self.recent_ids.pop(0)

        author = d.get("author") or {}
        if self.cfg.get_bool("debug.log-events", False):
            log.info("收到群 @ 消息：group=" + str(d["group_openid"]) +
                     " user=" + str(author.get("id")) +
                     " role=" + str(author.get("member_role") or "-") +
                     " content=" + json.dumps(d.get("content") or ""))

        message = {
            "id": d["id"],
            "groupId": d["group_openid"],
            "content": d.get("content") or "",
            "userId": author.get("id"),
            "username": author.get("username"),
            "memberRole": author.get("member_role"),
            "timestamp": d.get("timestamp"),
        }
        if self.on_group_message:
            self.on_group_message(message)

    # ---- access_token ----

    def get_access_token(self):
        with self._token_lock:
            if self.access_token and time.time() < self.token_expire_at - TOKEN_REFRESH_LEAD:
                return self.access_token
            return self._refresh_token()

    def _refresh_token(self):
        log.info("正在获取 access_token…")
        result = request_json("bots.qq.com", "/app/getAppAccessToken", method="POST",
                              headers={"Content-Type": "application/json; charset=utf-8"},
                              body={"appId": self.app_id, "clientSecret": self.secret},
                              label="getAppAccessToken")
        if not result.get("access_token"):
            raise RuntimeError("token 接口未返回 access_token：" + json.dumps(result))
        expires_in = result.get("expires_in", 7200)
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = 7200
        self.access_token = result["access_token"]
        self.token_expire_at = time.time() + expires_in
        return self.access_token

    def _get_gateway_url(self, token):
        result = request_json(self.backend_host, "/gateway", method="GET",
                              headers={"Authorization": "QQBot " + token, "X-Union-Appid": self.app_id},
                              label="gateway")
        url = result.get("url")
        if not url:
            raise RuntimeError("网关地址为空")
        return url

    # ---- 发消息（串行队列 + 节流） ----

    def send_group_message(self, group_id, content, msg_id=None):
        if self.stopped:
            return
        self._send_queue.put((group_id, content, msg_id, 0))

    def send_markdown(self, group_id, markdown_content, msg_id=None):
        if self.stopped:
            return
        self._send_queue.put((group_id, markdown_content, msg_id, 2))

    def _sender_loop(self):
        while not self.stopped:
            item = self._send_queue.get()
            if item is None:
                break
            group_id, content, msg_id, msg_type = item
            try:
                self._send_group_message_sync(group_id, content, msg_id, msg_type)
                if self.cfg.get_bool("debug.log-events", False):
                    kind = "Markdown" if msg_type == 2 else "群消息"
                    log.info("群" + kind + " 已发送 group=" + str(group_id) +
                             " content=" + json.dumps(str(content))[:150])
            except Exception as e:
                kind = "Markdown" if msg_type == 2 else "群消息"
                log.error("群" + kind + " 发送失败 group=" + str(group_id) + "：" + str(e))
            time.sleep(SEND_GAP_MS)

    def _send_group_message_sync(self, group_id, content, msg_id, msg_type=0):
        token = self.get_access_token()
        group_id_enc = urllib.parse.quote(str(group_id), safe="")
        body = {"msg_type": msg_type}
        if msg_type == 2:
            body["markdown"] = {"content": content}
        else:
            body["content"] = content
        if msg_id:
            body["msg_id"] = msg_id
        return request_json(self.backend_host, "/v2/groups/" + group_id_enc + "/messages",
                            method="POST",
                            headers={
                                "Authorization": "QQBot " + token,
                                "X-Union-Appid": self.app_id,
                                "Content-Type": "application/json; charset=utf-8",
                            },
                            body=body,
                            label="sendGroupMessage")
