"""Client for the persistent SegFormer daemon (tools/segformer_daemon.py).

This lives in its own module, not in ticktalk_main.py, because TickTalk runs
each @SQify function in its own process with only that function's body:
module-level helpers in ticktalk_main.py are not defined there. segformer()
imports this inside its body, like the other SQs import their tools.
"""


def segformer_via_daemon(tiff_path: str, output_path: str,
                         socket_path: str = "/run/segformer/segformer.sock") -> bool:
    """Send an inference request to the persistent SegFormer daemon.

    Returns True on success. The daemon keeps the ONNX model resident in
    memory, cutting the per-cycle cold-start cost of ~10–20 s.
    """
    import json
    import os as _os
    import socket as _socket
    import stat as _stat

    req = json.dumps({"tiff_path": tiff_path, "output_path": output_path}) + "\n"
    try:
        if not _stat.S_ISSOCK(_os.lstat(socket_path).st_mode):
            print(f"⚠️ {socket_path} is not a Unix socket — skipping daemon")
            return False
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
            sock.settimeout(10)   # connect: fail fast if daemon isn't ready
            sock.connect(socket_path)
            sock.settimeout(120)  # read: generous budget for ONNX inference on Pi
            sock.sendall(req.encode())
            sock.shutdown(_socket.SHUT_WR)

            data = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)

        resp = json.loads(data.strip())
        if resp.get("status") == "ok":
            print(f"\n SegFormer daemon: {resp.get('inference_ms', '?')} ms\n")
            return True
        print(f"⚠️ SegFormer daemon error: {resp.get('message')}")
        return False
    except Exception as e:
        print(f"⚠️ Could not reach SegFormer daemon: {e}")
        return False
