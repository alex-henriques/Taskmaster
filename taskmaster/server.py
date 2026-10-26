from __future__ import annotations

import contextlib
import os
import selectors
import socket
from typing import TYPE_CHECKING

from taskmaster.protocol import (
    BAD_REQUEST,
    INTERNAL,
    UNKNOWN_COMMAND,
    LineBuffer,
    ProtocolError,
    encode,
    err,
)

if TYPE_CHECKING:
    from taskmaster.api import Api

NAMED = ("status", "start", "stop", "restart")
NO_ARGS = ("reload", "shutdown")
MAX_QUEUED = 1 << 20


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


class Connection:
    """One client: a line buffer in, an output buffer out, never blocking."""

    def __init__(self, server: Server, sock: socket.socket) -> None:
        self.server = server
        self.sock = sock
        self.lines = LineBuffer()
        self.out = bytearray()
        self.closed = False
        server.api.loop.sel.register(sock, selectors.EVENT_READ, self._on_event)

    def send(self, msg: dict) -> None:
        self.send_raw(encode(msg))

    def send_raw(self, data: bytes) -> None:
        if self.closed:
            return
        if len(self.out) + len(data) > MAX_QUEUED:
            self.close()
            return
        self.out += data
        self._update_interest()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(KeyError, ValueError):
            self.server.api.loop.sel.unregister(self.sock)
        self.sock.close()
        self.server.conns.discard(self)

    def flush(self) -> None:
        """Send what the socket takes right now; the rest stays queued."""
        if self.closed or not self.out:
            return
        try:
            sent = self.sock.send(self.out, socket.MSG_NOSIGNAL)
        except BlockingIOError:
            return
        except (BrokenPipeError, ConnectionResetError):
            self.close()
            return
        del self.out[:sent]

    def _update_interest(self) -> None:
        """EVENT_WRITE only while something waits: a writable socket is always ready."""
        events = selectors.EVENT_READ
        if self.out:
            events |= selectors.EVENT_WRITE
        self.server.api.loop.sel.modify(self.sock, events, self._on_event)

    def _on_event(self, _sock: socket.socket, mask: int) -> None:
        if self.closed:
            return
        if mask & selectors.EVENT_READ:
            self._read()
        if mask & selectors.EVENT_WRITE and not self.closed:
            self.flush()
            if not self.closed:
                self._update_interest()

    def _read(self) -> None:
        try:
            data = self.sock.recv(4096)
        except BlockingIOError:
            return
        except (BrokenPipeError, ConnectionResetError):
            self.close()
            return
        if not data:
            self.close()
            return
        try:
            self.lines.feed(data)
            while not self.closed and (msg := self.lines.next_message()) is not None:
                self._handle(msg)
        except ProtocolError as e:
            self.send(err(BAD_REQUEST, str(e)))
            self.flush()
            self.close()

    def _handle(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        args = msg.get("args", [])
        allowed = {"cmd", "args"} | ({"rows", "cols"} if cmd == "attach" else set())
        if not isinstance(cmd, str) or not _strings(args) or set(msg) - allowed:
            self.send(err(BAD_REQUEST, "cmd must be a string, args a list of strings"))
            return
        if cmd not in NAMED + NO_ARGS:
            self.send(err(UNKNOWN_COMMAND, f"unknown command: {cmd}"))
            return
        if cmd in NO_ARGS and args:
            self.send(err(BAD_REQUEST, f"{cmd} takes no arguments"))
            return
        api = self.server.api
        try:
            reply = getattr(api, cmd)(args) if cmd in NAMED else getattr(api, cmd)()
        except Exception as e:
            api.loop.log.exception("error in %s", cmd)
            reply = err(INTERNAL, str(e))
        self.send(reply)


class Server:
    """The daemon's UNIX socket, served from the core's own loop."""

    def __init__(self, path: str, api: Api) -> None:
        self.path = path
        self.api = api
        self.conns: set[Connection] = set()
        self._check_stale(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self.sock.bind(path)
        except BaseException:
            self.sock.close()
            raise
        finally:
            os.umask(old)
        self.sock.listen()
        self.sock.setblocking(False)

    @staticmethod
    def _check_stale(path: str) -> None:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(path)
        except FileNotFoundError:
            pass
        except ConnectionRefusedError:
            os.unlink(path)
        else:
            raise RuntimeError("taskmaster is already running")
        finally:
            probe.close()

    def start(self) -> None:
        self.api.loop.sel.register(self.sock, selectors.EVENT_READ, self._accept)

    def _accept(self, sock: socket.socket, _mask: int) -> None:
        while True:
            try:
                client, _ = sock.accept()
            except BlockingIOError:
                return
            client.setblocking(False)
            self.conns.add(Connection(self, client))

    def close(self) -> None:
        for conn in list(self.conns):
            conn.flush()
            conn.close()
        with contextlib.suppress(KeyError, ValueError):
            self.api.loop.sel.unregister(self.sock)
        self.sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
