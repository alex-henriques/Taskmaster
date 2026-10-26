from __future__ import annotations

import socket

from taskmaster.protocol import (
    SOCKET_PATH,
    BackendError,
    LineBuffer,
    ProtocolError,
    encode,
)

LOST = "connection to taskmasterd lost"


class RemoteApi:
    """The Backend methods over the daemon's socket: one request, one reply."""

    def __init__(self, path: str = SOCKET_PATH) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise BackendError("taskmasterd is not running") from None
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

    def _call(self, cmd: str, args: list[str]) -> dict:
        try:
            self.sock.sendall(encode({"cmd": cmd, "args": args}))
            while (reply := self.lines.next_message()) is None:
                data = self.sock.recv(4096)
                if not data:
                    raise BackendError(LOST)
                self.lines.feed(data)
        except (OSError, ProtocolError):
            raise BackendError(LOST) from None
        return reply
