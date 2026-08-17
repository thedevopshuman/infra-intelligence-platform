"""Trusted no-network relay between one Docker-volume socket and host stdio."""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path


SOCKET_PATH = Path("/run/iip-mediation/request.sock")
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 4_194_304


def canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def read_socket_request(connection: socket.socket) -> Mapping[str, object]:
    received = bytearray()
    while b"\n" not in received:
        chunk = connection.recv(8192)
        if not chunk:
            raise ValueError
        received.extend(chunk)
        if len(received) > MAX_REQUEST_BYTES:
            raise ValueError
    line, suffix = bytes(received).split(b"\n", 1)
    document = json.loads(line)
    if suffix or not isinstance(document, dict):
        raise ValueError
    return document


def read_host_response() -> Mapping[str, object]:
    line = sys.stdin.buffer.readline(MAX_RESPONSE_BYTES + 2)
    if not line or len(line) > MAX_RESPONSE_BYTES + 1 or not line.endswith(b"\n"):
        raise ValueError
    document = json.loads(line)
    if not isinstance(document, dict):
        raise ValueError
    return document


def main() -> int:
    if sys.argv[1:] == ["--initialize"]:
        try:
            os.chmod(SOCKET_PATH.parent, 0o733)
            return 0
        except OSError:
            return 2
    if sys.argv[1:]:
        return 2
    try:
        SOCKET_PATH.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o777)
        server.listen(16)
    except (OSError, ValueError):
        return 2
    sys.stdout.buffer.write(canonical({"status": "ready"}) + b"\n")
    sys.stdout.buffer.flush()
    while True:
        try:
            connection, _ = server.accept()
            with connection:
                request = read_socket_request(connection)
                sys.stdout.buffer.write(canonical(request) + b"\n")
                sys.stdout.buffer.flush()
                response = read_host_response()
                connection.sendall(canonical(response) + b"\n")
        except (BrokenPipeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
