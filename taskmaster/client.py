from __future__ import annotations

import socket

from taskmaster.protocol import (
    SOCKET_PATH,
    BackendError,
    Channel,
    LineBuffer,
    ProtocolError,
    encode,
)

LOST = "connection to taskmasterd lost"


def _connect(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise BackendError("taskmasterd is not running") from None
    return sock


def _exchange(sock: socket.socket, lines: LineBuffer, request: dict) -> dict:
    """Send one request line, read until one reply line is complete."""
    try:
        sock.sendall(encode(request))
        while (reply := lines.next_message()) is None:
            data = sock.recv(4096)
            if not data:
                raise BackendError(LOST)
            lines.feed(data)
    except (OSError, ProtocolError):
        raise BackendError(LOST) from None
    return reply


class RemoteApi:
    """The Backend methods over the daemon's socket: one request, one reply."""

    def __init__(self, path: str = SOCKET_PATH) -> None:
        self.path = path
        self.sock = _connect(path)
        self.lines = LineBuffer()

    def close(self) -> None:
        self.sock.close()

    def status(self, names: list[str]) -> dict:
        return self._call("status", names)

    def start(self, names: list[str]) -> dict:
        return self._call("start", names)

    def stop(self, names: list[str]) -> dict:
        return self._call("stop", names)

    def restart(self, names: list[str]) -> dict:
        return self._call("restart", names)

    def reload(self) -> dict:
        return self._call("reload", [])

    def shutdown(self) -> dict:
        return self._call("shutdown", [])

    def attach(self, name: str, rows: int, cols: int) -> tuple[dict, Channel | None]:
        """A new connection of its own: after the reply it carries raw bytes."""
        sock = _connect(self.path)
        lines = LineBuffer()
        request = {"cmd": "attach", "args": [name], "rows": rows, "cols": cols}
        try:
            reply = _exchange(sock, lines, request)
        except BaseException:
            sock.close()
            raise
        if not reply["ok"]:
            sock.close()
            return reply, None
        return reply, Channel(sock, lines.buf)

    def _call(self, cmd: str, args: list[str]) -> dict:
        return _exchange(self.sock, self.lines, {"cmd": cmd, "args": args})
