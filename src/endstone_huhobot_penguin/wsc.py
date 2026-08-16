"""
零依赖 RFC6455 WebSocket 客户端，基于 Python 标准库 socket + ssl 实现。
专为 QQ 开放平台 wss 网关设计，不依赖任何第三方包。

用法：
    ws = WSC(url, headers={...})
    ws.connect()                       # 阻塞：建立 TCP/TLS 连接并完成握手
    frame = ws.recv_frame()            # 阻塞：返回 (opcode, payload) 或 None（连接关闭）
    ws.send_text("...")                # 发送文本帧
    ws.close(1000, "reason")           # 关闭连接（会解除 recv_frame 的阻塞）

说明：断线后不会自动重连，由上层（qqclient）决定 Resume 还是重新 Identify。
"""

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WSC:
    def __init__(self, url, headers=None, connect_timeout=30, max_payload=16 * 1024 * 1024):
        self.parsed = urlparse(url)
        if self.parsed.scheme not in ("ws", "wss"):
            raise ValueError("不支持的协议: " + self.parsed.scheme)
        self.is_secure = self.parsed.scheme == "wss"
        self.host = self.parsed.hostname
        self.port = self.parsed.port or (443 if self.is_secure else 80)
        self.path = self.parsed.path or "/"
        if self.parsed.query:
            self.path += "?" + self.parsed.query
        self.headers = headers or {}
        self.connect_timeout = connect_timeout
        self.max_payload = max_payload

        self.sock = None
        self.connected = False
        self._buf = bytearray()
        self._send_lock = threading.Lock()
        self._fragments = []
        self._fragment_opcode = 0

    # ---- 连接 / 握手 ----

    def connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        if self.is_secure:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=self.host)
        sock.settimeout(self.connect_timeout)
        self.sock = sock

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if self.is_secure else 80
        port_suffix = "" if self.port == default_port else ":" + str(self.port)
        lines = [
            "GET " + self.path + " HTTP/1.1",
            "Host: " + self.host + port_suffix,
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: " + key,
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in self.headers.items():
            lines.append(str(name) + ": " + str(value))
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        sock.sendall(request)

        # 读取握手响应直到空行
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("握手失败：连接被关闭")
            data += chunk
        header_end = data.index(b"\r\n\r\n")
        header_text = bytes(data[:header_end]).decode("utf-8", "replace")
        remainder = bytes(data[header_end + 4:])

        lines = header_text.split("\r\n")
        if not lines or not lines[0].startswith("HTTP/1."):
            raise ConnectionError("网关未返回 HTTP 状态行：" + (lines[0] if lines else "(空响应)"))
        if " 101" not in lines[0]:
            raise ConnectionError("网关未返回 101 升级：" + lines[0])

        expect = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
        accept = None
        for line in lines[1:]:
            if ":" in line and line.split(":", 1)[0].strip().lower() == "sec-websocket-accept":
                accept = line.split(":", 1)[1].strip()
                break
        if accept != expect:
            raise ConnectionError("Sec-WebSocket-Accept 校验失败")

        self.connected = True
        if remainder:
            self._buf += remainder
        # 握手完成后进入阻塞读取模式（心跳/重连由上层线程负责）
        try:
            sock.settimeout(None)
        except OSError:
            pass

    def is_open(self):
        return self.connected and self.sock is not None

    # ---- 接收 ----

    def recv_frame(self):
        """阻塞返回 (opcode, payload) 完整数据帧；连接关闭时返回 None。"""
        while True:
            frame = self._parse_frame()
            if frame is not None:
                result = self._handle_frame(frame)
                if result == "CLOSE":
                    return None
                if result is not None:
                    return result
                continue
            if not self.sock:
                return None
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk

    def _parse_frame(self):
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        idx = 2
        if length == 126:
            if len(buf) < 4:
                return None
            length = struct.unpack("!H", bytes(buf[2:4]))[0]
            idx = 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length = struct.unpack("!Q", bytes(buf[2:10]))[0]
            idx = 10
        if length > self.max_payload:
            raise ConnectionError("单帧负载超出上限 " + str(self.max_payload) + " 字节")

        mask_key = b""
        if masked:
            if len(buf) < idx + 4:
                return None
            mask_key = bytes(buf[idx:idx + 4])
            idx += 4
        if len(buf) < idx + length:
            return None

        payload = bytes(buf[idx:idx + length])
        if masked:
            payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        del self._buf[:idx + length]
        return {"fin": fin, "opcode": opcode, "payload": payload}

    def _handle_frame(self, frame):
        opcode = frame["opcode"]
        payload = frame["payload"]
        fin = frame["fin"]

        if opcode == OP_PING:
            self._send_frame(OP_PONG, payload)
            return None
        if opcode == OP_PONG:
            return None
        if opcode == OP_CLOSE:
            try:
                self._send_frame(OP_CLOSE, payload)
            except Exception:
                pass
            self.connected = False
            return "CLOSE"
        if opcode in (OP_TEXT, OP_BINARY):
            if not fin:
                if self._fragment_opcode != 0:
                    raise ConnectionError("重叠分片")
                self._fragments = [payload]
                self._fragment_opcode = opcode
                return None
            return (opcode, payload)
        if opcode == OP_CONTINUATION:
            if self._fragment_opcode == 0:
                return None  # 孤立续帧，忽略
            self._fragments.append(payload)
            if fin:
                full = b"".join(self._fragments)
                op = self._fragment_opcode
                self._fragments = []
                self._fragment_opcode = 0
                return (op, full)
            return None
        raise ConnectionError("未知 opcode: " + str(opcode))

    # ---- 发送（客户端帧一律掩码） ----

    def send_text(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self._send_frame(OP_TEXT, data)

    def _send_frame(self, opcode, payload):
        payload = bytes(payload)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        with self._send_lock:
            if not self.sock or not self.connected:
                return False
            try:
                self.sock.sendall(header + mask + masked)
                return True
            except OSError:
                return False

    def close(self, code=1000, reason=""):
        if self.sock is None:
            return
        if isinstance(reason, str):
            reason_bytes = reason.encode("utf-8")
        else:
            reason_bytes = bytes(reason)
        payload = struct.pack("!H", code) + reason_bytes
        self._send_frame(OP_CLOSE, payload)
        self.connected = False
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None
