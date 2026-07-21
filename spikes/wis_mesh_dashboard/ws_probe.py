#!/usr/bin/env python3
"""ws_probe.py — minimal RFC 6455 WebSocket client for the wis_mesh_dashboard spike.

Neither the `websockets` Python package nor a `wscat`/similar CLI client is installed on
this VM (checked at spike-build time: `python3 -c "import websockets"` fails, `which wscat`
finds nothing). Rather than skip the WebSocket check, this implements just enough of RFC
6455 by hand: the HTTP Upgrade handshake, masked client->server text frames, and unmasked
server->client frame decoding (text + ping/pong + close) — no external dependency.

Also implements RTI Web Integration Service's own application-level handshake, taken
verbatim from the ONLY real reference in this environment —
$NDDSHOME/resource/template/rti_workspace/examples/web_integration_service/websockets/simple_shapes_demo/js/shapes_demo.js
(the shipped ShapesDemo JS client):
  1. After the WS opens, send a "HELLO" text frame (Content-Type/Accept/Version headers as
     a raw text blob, NOT JSON).
  2. Send a `{"kind": "bind", "body": [{"bind_kind": "bind_datareader", "bind_id": ...,
     "uri": ...}]}` JSON text frame naming the DataReader resource URI to bind to.
  3. Server pushes samples as JSON text frames from then on.

See README.md's "WebSocket: established facts vs. reality" section for how this differs
from the WIS facts assumed before this spike ran.
"""
import argparse
import base64
import json
import os
import socket
import struct
import sys
import time


def ws_handshake(sock, host, port, path):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("connection closed during WS handshake")
        resp += chunk
    header, _, rest = resp.partition(b"\r\n\r\n")
    status_line = header.split(b"\r\n")[0]
    if b"101" not in status_line:
        raise RuntimeError(f"WS handshake failed: {status_line!r}; headers={header!r}")
    return bytearray(rest)  # any body bytes already read past the header


def send_text_frame(sock, payload):
    data = payload.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    length = len(data)
    header = bytearray([0x81])  # FIN + text opcode
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += mask
    sock.sendall(bytes(header) + masked)


def send_pong(sock, payload):
    header = bytearray([0x8A, 0x80 | len(payload)])
    mask = os.urandom(4)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _fill(sock, buf, n, deadline):
    while len(buf) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return False
        if not chunk:
            return False
        buf += chunk
    return True


def recv_frames(sock, buf, deadline):
    """Yields decoded text-frame payloads until deadline (time.monotonic())."""
    while time.monotonic() < deadline:
        if not _fill(sock, buf, 2, deadline):
            return
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        plen = b1 & 0x7F
        idx = 2
        if plen == 126:
            if not _fill(sock, buf, idx + 2, deadline):
                return
            plen = struct.unpack(">H", bytes(buf[idx:idx + 2]))[0]
            idx += 2
        elif plen == 127:
            if not _fill(sock, buf, idx + 8, deadline):
                return
            plen = struct.unpack(">Q", bytes(buf[idx:idx + 8]))[0]
            idx += 8
        mask_key = b""
        if masked:
            if not _fill(sock, buf, idx + 4, deadline):
                return
            mask_key = bytes(buf[idx:idx + 4])
            idx += 4
        if not _fill(sock, buf, idx + plen, deadline):
            return
        payload = bytes(buf[idx:idx + plen])
        if masked:
            payload = bytes(c ^ mask_key[i % 4] for i, c in enumerate(payload))
        del buf[:idx + plen]
        if opcode == 0x1:
            yield payload.decode(errors="replace")
        elif opcode == 0x9:
            send_pong(sock, payload)
        elif opcode == 0x8:
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ws-path", required=True, help="e.g. /dds/websocket/MeshWsConn")
    ap.add_argument("--reader-uri", required=True,
                     help="the REST resource path of the DataReader to bind, e.g. "
                          "/dds/rest1/applications/.../data_readers/RouterHealthReader")
    ap.add_argument("--bind-id", default="RouterHealthReader")
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5.0)
    try:
        buf = ws_handshake(sock, args.host, args.port, args.ws_path)

        hello = ("Content-Type:application/dds-web+json\r\n"
                  "Accept:application/dds-web+json\r\n"
                  "OMG-DDS-API-Key:\r\n"
                  "Version:1\r\n\r\n")
        send_text_frame(sock, hello)

        bind_msg = json.dumps({
            "kind": "bind",
            "body": [{
                "bind_kind": "bind_datareader",
                "bind_id": args.bind_id,
                "uri": args.reader_uri,
            }],
        })
        send_text_frame(sock, bind_msg)

        deadline = time.monotonic() + args.timeout
        messages = []
        got_push = False
        for msg in recv_frames(sock, buf, deadline):
            messages.append(msg)
            # The initial bind ack and any keepalive frames don't carry sample data; a
            # real push has a "read_sample_seq" body (WS wraps samples differently than
            # the flat-array REST response — see README).
            if '"read_sample_seq"' in msg and '"data"' in msg:
                got_push = True
                break

        print(json.dumps({"got_push": got_push, "n_messages": len(messages),
                           "messages": messages[:5]}))
        sys.exit(0 if got_push else 1)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
