from __future__ import annotations

import os
import selectors
import socket
import stat
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from taskmaster.protocol import (
    BAD_REQUEST,
    INTERNAL,
    NOT_TTY,
    UNKNOWN_COMMAND,
    LineBuffer,
    encode,
    err,
    ok,
)
from taskmaster.server import Server


class FakeProcess:
    """What the server needs of a Process: attach, detach and write_input."""

    def __init__(self) -> None:
        self.sinks: list = []
        self.typed = b""
        self.detached = 0

    def attach(self, sink, rows: int, cols: int) -> None:
        self.size = (rows, cols)
        sink.send_raw(b"replay")
        self.sinks.append(sink)

    def detach(self, sink) -> None:
        self.detached += 1

    def write_input(self, data: bytes) -> None:
        self.typed += data


class FakeApi:
    """Records the calls; every reply carries the command name in msg."""

    def __init__(self) -> None:
        self.loop = SimpleNamespace(sel=selectors.DefaultSelector(), log=None)
        self.calls: list[tuple] = []
        self.process = FakeProcess()
        for name in ("status", "start", "stop", "restart"):
            setattr(self, name, self._named(name))
        for name in ("reload", "shutdown"):
            setattr(self, name, self._bare(name))

    def get_tty(self, name: str):
        self.calls.append(("get_tty", name))
        if name == "plain":
            return err(NOT_TTY, "plain does not run in a tty"), None
        return ok(), self.process

    def _named(self, name: str):
        def method(names: list[str]) -> dict:
            self.calls.append((name, names))
            return ok(msg=name)

        return method

    def _bare(self, name: str):
        def method() -> dict:
            self.calls.append((name, ()))
            return ok(msg=name)

        return method


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "s")
        self.api = FakeApi()

    def serve(self) -> Server:
        server = Server(self.path, self.api)
        server.start()
        self.addCleanup(server.close)
        return server

    def connect(self) -> tuple[socket.socket, LineBuffer]:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.connect(self.path)
        client.setblocking(False)
        return client, LineBuffer()

    def pump(self, until, timeout: float = 2.0) -> None:
        end = time.monotonic() + timeout
        while not until():
            if time.monotonic() > end:
                self.fail("the server never got there")
            for key, mask in self.api.loop.sel.select(0.02):
                key.data(key.fileobj, mask)

    def replies(self, client: socket.socket, lines: LineBuffer, count: int) -> list:
        got: list[dict] = []

        def done() -> bool:
            try:
                data = client.recv(4096)
            except BlockingIOError:
                data = b""
            lines.feed(data)
            while (msg := lines.next_message()) is not None:
                got.append(msg)
            return len(got) >= count

        self.pump(done)
        return got

    def test_stale_socket_is_removed(self) -> None:
        old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old.bind(self.path)
        old.close()
        self.assertTrue(os.path.exists(self.path))
        self.serve()
        client, lines = self.connect()
        client.sendall(encode({"cmd": "status", "args": []}))
        self.assertEqual(self.replies(client, lines, 1), [ok(msg="status")])

    def test_second_server_is_refused(self) -> None:
        self.serve()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            Server(self.path, self.api)
        self.assertTrue(os.path.exists(self.path))

    def test_mode_and_unlink(self) -> None:
        server = self.serve()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        server.close()
        self.assertFalse(os.path.exists(self.path))

    def test_split_request(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(b'{"cmd": "sta')
        for _ in range(5):
            self.api.loop.sel.select(0.02)
        client.sendall(b'rt", "args": ["a"]}\n')
        self.assertEqual(self.replies(client, lines, 1), [ok(msg="start")])
        self.assertEqual(self.api.calls, [("start", ["a"])])

    def test_two_requests_one_send(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(encode({"cmd": "reload"}) + encode({"cmd": "shutdown"}))
        self.assertEqual(
            self.replies(client, lines, 2), [ok(msg="reload"), ok(msg="shutdown")]
        )

    def test_bad_requests(self) -> None:
        self.serve()
        client, lines = self.connect()
        for msg in (
            {"cmd": "start", "args": "x"},
            {"cmd": "start", "args": [1]},
            {"cmd": 3, "args": []},
            {"cmd": "status", "args": [], "extra": 1},
            {"cmd": "status", "args": [], "rows": 1},
            {"cmd": "reload", "args": ["x"]},
        ):
            client.sendall(encode(msg))
            self.assertEqual(self.replies(client, lines, 1)[-1]["error"], BAD_REQUEST)
        client.sendall(encode({"cmd": "dance", "args": []}))
        reply = self.replies(client, lines, 1)[-1]
        self.assertEqual(reply["error"], UNKNOWN_COMMAND)
        self.assertEqual(self.api.calls, [])

    def test_garbage_gets_bad_request_then_eof(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(b"nope\n")
        self.assertEqual(self.replies(client, lines, 1)[0]["error"], BAD_REQUEST)

        def eof() -> bool:
            try:
                return client.recv(1) == b""
            except BlockingIOError:
                return False

        self.pump(eof)

    def test_dead_client_does_not_affect_another(self) -> None:
        self.serve()
        gone, _ = self.connect()
        other, lines = self.connect()
        gone.sendall(encode({"cmd": "status", "args": []}))
        gone.close()
        other.sendall(encode({"cmd": "stop", "args": ["x"]}))
        self.assertEqual(self.replies(other, lines, 1), [ok(msg="stop")])
        other.sendall(encode({"cmd": "restart", "args": ["x"]}))
        self.assertEqual(self.replies(other, lines, 1), [ok(msg="restart")])

    def attach_request(self, name: str = "t", rows: int = 24, cols: int = 80) -> bytes:
        return encode({"cmd": "attach", "args": [name], "rows": rows, "cols": cols})

    def drain(self, client: socket.socket, lines: LineBuffer) -> bool:
        try:
            lines.buf += client.recv(4096)
        except BlockingIOError:
            pass
        return lines.buf == b"replay"

    def test_attach_replies_then_goes_raw(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request(rows=40, cols=120))
        self.assertEqual(self.replies(client, lines, 1), [ok()])
        self.pump(lambda: self.api.process.sinks)
        self.assertEqual(self.api.process.size, (40, 120))
        self.pump(lambda: self.drain(client, lines))
        self.assertEqual(lines.buf, b"replay")
        client.sendall(b"hello")
        self.pump(lambda: self.api.process.typed == b"hello")

    def test_input_right_behind_the_request_is_not_json(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request() + b"typed fast")
        self.assertEqual(self.replies(client, lines, 1), [ok()])
        self.pump(lambda: self.api.process.typed == b"typed fast")

    def test_closing_a_raw_connection_detaches(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request())
        self.replies(client, lines, 1)
        client.close()
        self.pump(lambda: self.api.process.detached == 1)

    def test_not_a_tty_is_a_reply_and_the_connection_stays_json(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request("plain"))
        self.assertEqual(self.replies(client, lines, 1)[0]["error"], NOT_TTY)
        client.sendall(encode({"cmd": "status", "args": []}))
        self.assertEqual(self.replies(client, lines, 1), [ok(msg="status")])
        self.assertEqual(self.api.process.sinks, [])

    def test_an_error_in_get_tty_still_gets_a_reply(self) -> None:
        self.api.loop.log = mock.Mock()
        self.api.get_tty = mock.Mock(side_effect=RuntimeError("boom"))
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request())
        self.assertEqual(self.replies(client, lines, 1), [err(INTERNAL, "boom")])

    def test_bad_attach_requests(self) -> None:
        self.serve()
        client, lines = self.connect()
        for msg in (
            {"cmd": "attach", "args": []},
            {"cmd": "attach", "args": ["a", "b"], "rows": 1, "cols": 1},
            {"cmd": "attach", "args": ["a"], "rows": 24},
            {"cmd": "attach", "args": ["a"], "rows": -1, "cols": 80},
            {"cmd": "attach", "args": ["a"], "rows": "24", "cols": 80},
            {"cmd": "attach", "args": ["a"], "rows": True, "cols": 80},
            {"cmd": "attach", "args": ["a"], "rows": 24, "cols": 70000},
        ):
            client.sendall(encode(msg))
            self.assertEqual(self.replies(client, lines, 1)[-1]["error"], BAD_REQUEST)
        self.assertEqual(self.api.process.sinks, [])

    def test_more_than_1_mib_queued_closes_and_detaches(self) -> None:
        self.serve()
        client, lines = self.connect()
        client.sendall(self.attach_request())
        self.replies(client, lines, 1)
        self.pump(lambda: self.api.process.sinks)
        conn = self.api.process.sinks[0]
        conn.send_raw(b"x" * ((1 << 20) + 1))
        self.assertTrue(conn.closed)
        self.assertEqual(self.api.process.detached, 1)


if __name__ == "__main__":
    unittest.main()
