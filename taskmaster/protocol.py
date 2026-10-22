from __future__ import annotations

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


class BackendError(Exception):
    """The backend is gone: the core stopped, or the connection was lost."""


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
