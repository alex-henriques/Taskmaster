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
    from collections.abc import Callable

    from taskmaster.api import Api
    from taskmaster.core.process import Process

NAMED = ("status", "start", "stop", "restart")
NO_ARGS = ("reload", "shutdown")
MAX_QUEUED = 1 << 20
MAX_SIZE = 0xFFFF  # a window size is two unsigned shorts


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _size(value: object) -> bool:
    """A window dimension: an int (not a bool) that fits the ioctl. 0 means unknown."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SIZE
    )


class Connection:
    """One client: a line buffer in, an output buffer out, never blocking.

    After an attach it is raw: what comes in goes to the process, what the process
    prints goes out. It is a Sink for the process.
    """

    def __init__(
        self,
        api: Api,
        sock: socket.socket,
        on_close: Callable[[Connection], None] | None = None,
    ) -> None:
        self.api = api
        self.sock = sock
        self.on_close = on_close
        self.lines = LineBuffer()
        self.out = bytearray()
        self.closed = False
        self.process: Process | None = None
        api.loop.sel.register(sock, selectors.EVENT_READ, self._on_event)

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
        # the last output of a program that just died
        with contextlib.suppress(OSError):
            self._send_some()
        if self.process is not None:
            self.process.detach(self)
            self.process = None
        with contextlib.suppress(KeyError, ValueError):
            self.api.loop.sel.unregister(self.sock)
        self.sock.close()
        if self.on_close is not None:
            self.on_close(self)

    def start_attach(self, process: Process, rows: int, cols: int) -> None:
        """Go raw: from now on this connection is the process's console."""
        self.process = process
        process.attach(self, rows, cols)

    def _send_some(self) -> None:
        if self.out:
            sent = self.sock.send(self.out, socket.MSG_NOSIGNAL)
            del self.out[:sent]

    def flush(self) -> None:
        """Send what the socket takes right now; the rest stays queued."""
        if self.closed:
            return
        try:
            self._send_some()
        except BlockingIOError:
            return
        except OSError:  # BrokenPipeError, ConnectionResetError
            self.close()

    def _update_interest(self) -> None:
        """EVENT_WRITE only while something waits: a writable socket is always ready."""
        events = selectors.EVENT_READ
        if self.out:
            events |= selectors.EVENT_WRITE
        self.api.loop.sel.modify(self.sock, events, self._on_event)

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
        if self.process is not None:
            self.process.write_input(data)
            return
        try:
            self.lines.feed(data)
            while (
                not self.closed
                and self.process is None
                and (msg := self.lines.next_message()) is not None
            ):
                self._handle(msg)
            if self.process is not None and self.lines.buf:
                # typed right behind the attach request: it is input, not JSON
                self.process.write_input(self.lines.buf)
                self.lines.buf = b""
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
        if cmd == "attach":
            self._attach(args, msg)
            return
        if cmd not in NAMED + NO_ARGS:
            self.send(err(UNKNOWN_COMMAND, f"unknown command: {cmd}"))
            return
        if cmd in NO_ARGS and args:
            self.send(err(BAD_REQUEST, f"{cmd} takes no arguments"))
            return
        api = self.api
        try:
            reply = getattr(api, cmd)(args) if cmd in NAMED else getattr(api, cmd)()
        except Exception as e:
            api.loop.log.exception("error in %s", cmd)
            reply = err(INTERNAL, str(e))
        self.send(reply)

    def _attach(self, args: list[str], msg: dict) -> None:
        if len(args) != 1 or not _size(msg.get("rows")) or not _size(msg.get("cols")):
            self.send(err(BAD_REQUEST, "attach takes one name, rows and cols"))
            return
        try:
            reply, process = self.api.get_tty(args[0])
        except Exception as e:
            # without a reply the client would wait forever: it ignores Ctrl-C meanwhile
            self.api.loop.log.exception("error in attach")
            reply, process = err(INTERNAL, str(e)), None
        self.send(reply)  # queued before anything the process prints
        if process is not None:
            self.start_attach(process, msg["rows"], msg["cols"])


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
            self.conns.add(Connection(self.api, client, self.conns.discard))

    def close(self) -> None:
        for conn in list(self.conns):
            conn.flush()
            conn.close()
        with contextlib.suppress(KeyError, ValueError):
            self.api.loop.sel.unregister(self.sock)
        self.sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
