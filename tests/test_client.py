from __future__ import annotations

import contextlib
import logging
import os
import signal
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from taskmaster.api import Api
from taskmaster.client import RemoteApi
from taskmaster.protocol import BAD_NAME, NOT_TTY, BackendError, encode, ok
from taskmaster.server import Server

CONFIG = """\
programs:
  sleeper:
    cmd: "sleep 30"
    autostart: false
    starttime: 0.2
  catter:
    cmd: "cat"
    tty: true
    starttime: 0.2
"""


class RemoteApiTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(tmp.name)
        self.path = os.path.join(tmp.name, "s")
        with open("c.yaml", "w") as f:
            f.write(CONFIG)
        self.api = Api("c.yaml", interactive=False)
        log = logging.getLogger("taskmaster")
        for handler in log.handlers[:]:
            self.addCleanup(handler.close)
            self.addCleanup(log.removeHandler, handler)

    def run_with(self, body) -> int:
        """Run the core and the server on this thread while body(remote) runs on another."""
        server = Server(self.path, self.api)
        server.start()

        def helper() -> None:
            remote = RemoteApi(self.path)
            try:
                body(remote)
            finally:
                with contextlib.suppress(BackendError):
                    remote.shutdown()
                remote.close()

        with ThreadPoolExecutor(1) as pool:
            done = pool.submit(helper)
            try:
                code = self.api.run()
            finally:
                server.close()
        done.result()
        return code

    def test_calls_match_the_api(self) -> None:
        seen = {}

        def body(remote: RemoteApi) -> None:
            seen["status"] = remote.status(["sleeper"])
            seen["direct"] = self.api.status(["sleeper"])
            seen["bad"] = remote.start(["nope"])
            seen["start"] = remote.start(["sleeper"])
            seen["stop"] = remote.stop(["sleeper"])

        self.assertEqual(self.run_with(body), 0)
        self.assertEqual(seen["status"], seen["direct"])
        self.assertEqual(seen["bad"]["error"], BAD_NAME)
        self.assertEqual(seen["start"]["data"][0]["msg"], "starting")
        self.assertEqual(seen["stop"]["data"][0]["msg"], "stopping")

    def test_shutdown_ends_run_and_removes_the_socket(self) -> None:
        replies = []

        def body(remote: RemoteApi) -> None:
            replies.append(remote.shutdown())

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(replies[0]["ok"])
        self.assertFalse(os.path.exists(self.path))

    def console(self, channel, want: bytes, timeout: float = 5.0) -> bytes:
        """Read the console until `want` shows up."""
        channel.sock.settimeout(timeout)
        seen = channel.pending
        while want not in seen:
            seen += channel.sock.recv(4096)
        return seen

    def test_attach_through_the_socket(self) -> None:
        seen = {}

        def body(remote: RemoteApi) -> None:
            reply, channel = remote.attach("catter", 24, 80)
            seen["reply"] = reply
            channel.sock.sendall(b"hello\n")
            seen["echo"] = self.console(channel, b"hello\r\nhello")
            channel.sock.close()
            seen["plain"] = remote.attach("sleeper", 24, 80)
            seen["bad"] = remote.attach("nope", 24, 80)
            seen["still json"] = remote.status(["sleeper"])

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(seen["reply"]["ok"])
        self.assertIn(b"hello\r\nhello", seen["echo"])
        self.assertEqual(seen["plain"][1], None)
        self.assertEqual(seen["plain"][0]["error"], NOT_TTY)
        self.assertEqual(seen["bad"][0]["error"], BAD_NAME)
        self.assertTrue(seen["still json"]["ok"])

    def test_attach_in_process(self) -> None:
        seen = {}

        def body(remote: RemoteApi) -> None:
            reply, channel = self.api.attach("catter", 24, 80)
            seen["reply"] = reply
            channel.sock.sendall(b"hi\n")
            seen["echo"] = self.console(channel, b"hi\r\nhi")
            seen["plain"] = self.api.attach("sleeper", 24, 80)
            seen["bad"] = self.api.attach("nope", 24, 80)
            channel.sock.close()

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(seen["reply"]["ok"])
        self.assertIn(b"hi\r\nhi", seen["echo"])
        self.assertEqual(seen["plain"][1], None)
        self.assertEqual(seen["plain"][0]["error"], NOT_TTY)
        self.assertEqual(seen["bad"][0]["error"], BAD_NAME)

    def test_the_program_dying_ends_the_console(self) -> None:
        seen = {}

        def body(remote: RemoteApi) -> None:
            _, channel = remote.attach("catter", 24, 80)
            remote.stop(["catter"])
            channel.sock.settimeout(5)
            data = b"x"
            while data:
                data = channel.sock.recv(4096)
            channel.sock.close()
            seen["eof"] = True

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(seen["eof"])

    def test_no_daemon(self) -> None:
        with self.assertRaisesRegex(BackendError, "taskmasterd is not running"):
            RemoteApi(self.path)

    def test_connection_closed_in_the_middle_of_a_request(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(self.path)
        listener.listen()

        def hang_up() -> None:
            conn, _ = listener.accept()
            conn.recv(4096)
            conn.close()

        thread = threading.Thread(target=hang_up)
        thread.start()
        self.addCleanup(thread.join)
        remote = RemoteApi(self.path)
        self.addCleanup(remote.close)
        with self.assertRaisesRegex(BackendError, "connection to taskmasterd lost"):
            remote.status([])

    def test_ctrl_c_while_waiting_keeps_the_reply(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(self.path)
        listener.listen()

        def reply_after_ctrl_c() -> None:
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.2)
                conn.sendall(encode(ok(msg="mine")))

        thread = threading.Thread(target=reply_after_ctrl_c)
        thread.start()
        self.addCleanup(thread.join)
        remote = RemoteApi(self.path)
        self.addCleanup(remote.close)
        before = signal.getsignal(signal.SIGINT)
        try:
            reply = remote.status([])
        except KeyboardInterrupt:
            self.fail("Ctrl-C cut the request short")
        self.assertEqual(reply, ok(msg="mine"))
        self.assertIs(signal.getsignal(signal.SIGINT), before)


if __name__ == "__main__":
    unittest.main()
