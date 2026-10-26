from __future__ import annotations

import os
import selectors
import socket
import stat
import tempfile
import time
import unittest
from types import SimpleNamespace

from taskmaster.protocol import (
    BAD_REQUEST,
    UNKNOWN_COMMAND,
    LineBuffer,
    encode,
    ok,
)
from taskmaster.server import Server


class FakeApi:
    """Records the calls; every reply carries the command name in msg."""

    def __init__(self) -> None:
        self.loop = SimpleNamespace(sel=selectors.DefaultSelector(), log=None)
        self.calls: list[tuple] = []
        for name in ("status", "start", "stop", "restart"):
            setattr(self, name, self._named(name))
        for name in ("reload", "shutdown"):
            setattr(self, name, self._bare(name))

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


if __name__ == "__main__":
    unittest.main()
