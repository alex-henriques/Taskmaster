from __future__ import annotations

import contextlib
import logging
import os
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from taskmaster.api import Api
from taskmaster.client import RemoteApi
from taskmaster.protocol import BAD_NAME, BackendError
from taskmaster.server import Server

CONFIG = """\
programs:
  sleeper:
    cmd: "sleep 30"
    autostart: false
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


if __name__ == "__main__":
    unittest.main()
