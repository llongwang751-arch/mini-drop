"""有界假服务端，仅用于 Helper 客户端的损坏响应与身份校验回归。"""
import hashlib
import json
import os
import socket
import struct
import sys

_, operation, path, mode = sys.argv
if operation != "serve":
    raise SystemExit(2)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
    listener.bind(path)
    os.chmod(path, 0o660)
    listener.listen(1)
    listener.settimeout(10)
    with listener.accept()[0] as peer:
        if mode == "peer":
            raise SystemExit(0)
        peer.settimeout(3)
        header = peer.recv(4)
        if len(header) != 4:
            raise SystemExit(2)
        length = struct.unpack("!I", header)[0]
        if not 0 < length <= 65536:
            raise SystemExit(2)
        received = b""
        while len(received) < length:
            chunk = peer.recv(length - len(received))
            if not chunk:
                raise SystemExit(2)
            received += chunk
        data = b"explicitly imported transport corruption fixture\n"
        response = {"protocol_version": 1, "ok": True, "kind": "PERF_CPU", "name": "profile.perf-script",
                    "format": "perf_script", "content_type": "text/plain", "metadata": {},
                    "files": [{"name": "profile.perf-script", "kind": "RAW", "size": len(data),
                               "sha256": "0" * 64 if mode == "sha" else hashlib.sha256(data).hexdigest()}]}
        encoded = json.dumps(response).encode()
        try:
            peer.sendall(struct.pack("!I", len(encoded)) + encoded + data)
        except BrokenPipeError:
            pass
