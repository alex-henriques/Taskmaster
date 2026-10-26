from __future__ import annotations

import json
from typing import Protocol

BAD_REQUEST = "BAD_REQUEST"
UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
BAD_NAME = "BAD_NAME"
ALREADY_STARTED = "ALREADY_STARTED"
NOT_RUNNING = "NOT_RUNNING"
BUSY = "BUSY"
BAD_CONFIG = "BAD_CONFIG"
NOT_TTY = "NOT_TTY"
INTERNAL = "INTERNAL"
SOCKET_PATH = "/tmp/taskmaster.sock"
MAX_LINE = 64 * 1024


class BackendError(Exception):
    """The backend is gone: the core stopped, or the connection was lost."""


class ProtocolError(Exception):
    """A line that is too long, or not a JSON object."""


def ok(data=None, msg: str | None = None) -> dict:
    reply: dict = {"ok": True}
    if data is not None:
        reply["data"] = data
    if msg is not None:
        reply["msg"] = msg
    return reply


def err(code: str, msg: str) -> dict:
    return {"ok": False, "error": code, "msg": msg}


class Backend(Protocol):
    def status(self, names: list[str]) -> dict: ...
    def start(self, names: list[str]) -> dict: ...
    def stop(self, names: list[str]) -> dict: ...
    def restart(self, names: list[str]) -> dict: ...
    def reload(self) -> dict: ...
    def shutdown(self) -> dict: ...


def encode(msg: dict) -> bytes:
    """One compact JSON line. ensure_ascii escapes any newline inside a string"""
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


class LineBuffer:
    """Bytes received and not consumed yet, next_message pops one line at a time."""

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> None:
        self.buf += data
        if len(self.buf) - (self.buf.rfind(b"\n") + 1) > MAX_LINE:
            raise ProtocolError(f"line longer than {MAX_LINE} bytes")

    def next_message(self) -> dict | None:
        end = self.buf.find(b"\n")
        if end == -1:
            return None
        line, self.buf = self.buf[:end], self.buf[end + 1 :]
        if len(line) > MAX_LINE:
            raise ProtocolError(f"line longer than {MAX_LINE} bytes")
        try:
            msg = json.loads(line)
        except ValueError as e:
            raise ProtocolError(f"not JSON: {e}") from e
        if not isinstance(msg, dict):
            raise ProtocolError("not a JSON object")
        return msg
