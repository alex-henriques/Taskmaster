from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from taskmaster.api import Api
from taskmaster.core.config import ConfigError
from taskmaster.protocol import BAD_NAME, BackendError, err

CONFIG = """\
programs:
  sleeper:
    cmd: "sleep 30"
    autostart: false
    starttime: 0.2
"""


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(tmp.name)
        with open("c.yaml", "w") as f:
            f.write(CONFIG)
        self.api = Api("c.yaml", interactive=True)
        log = logging.getLogger("taskmaster")
        handler = log.handlers[-1]
        self.addCleanup(handler.close)
        self.addCleanup(log.removeHandler, handler)

    def state(self) -> str:
        return self.api.status(["sleeper"])["data"][0]["state"]

    def wait_state(self, state: str, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while self.state() != state:
            if time.monotonic() > end:
                self.fail(f"never {state}: {self.state()}")
            time.sleep(0.02)

    def run_with(self, body) -> int:
        """Run the core on this thread while body() runs on a helper thread."""

        def helper() -> None:
            try:
                body()
            finally:
                with contextlib.suppress(BackendError):
                    self.api.shutdown()

        with ThreadPoolExecutor(1) as pool:
            done = pool.submit(helper)
            code = self.api.run()
        done.result()
        return code

    def test_init_logs_in_the_current_folder(self) -> None:
        self.assertTrue(os.path.exists(os.path.abspath("taskmaster.log")))

    def test_call_before_run_is_answered_once_the_loop_is_up(self) -> None:
        replies = []

        def body() -> None:
            replies.append(self.api.status([]))

        with ThreadPoolExecutor(1) as pool:
            done = pool.submit(lambda: (body(), self.api.shutdown()))
            time.sleep(0.2)
            self.assertEqual(replies, [])
            self.assertEqual(self.api.run(), 0)
        done.result()
        self.assertEqual(replies[0]["data"][0]["name"], "sleeper")

    def test_calls_return_what_the_supervisor_returns(self) -> None:
        seen = {}

        def body() -> None:
            seen["bad"] = self.api.start(["nope"])
            seen["start"] = self.api.start(["sleeper"])
            self.wait_state("RUNNING")
            seen["again"] = self.api.start(["sleeper"])
            seen["stop"] = self.api.stop(["all"])
            self.wait_state("STOPPED")
            seen["status"] = self.api.status([])

        self.assertEqual(self.run_with(body), 0)
        self.assertEqual(seen["bad"], err(BAD_NAME, "no such process: nope"))
        self.assertEqual(seen["start"]["data"][0]["msg"], "starting")
        self.assertEqual(seen["again"]["data"][0]["error"], "ALREADY_STARTED")
        self.assertEqual(seen["stop"]["data"][0]["msg"], "stopping")
        self.assertEqual(seen["status"]["data"][0]["state"], "STOPPED")

    def test_call_on_the_loop_thread_does_not_deadlock(self) -> None:
        reply = []

        def body() -> None:
            future = self.api.loop.submit(lambda: self.api.status([]))
            reply.append(future.result(timeout=5))

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(reply[0]["ok"])

    def test_a_flooding_program_does_not_slow_the_calls_down(self) -> None:
        seen = {}

        def body() -> None:
            with open("c.yaml", "a") as f:
                f.write('  flood:\n    cmd: "yes"\n    autostart: false\n')
            seen["reload"] = self.api.reload()
            self.api.start(["flood"])
            times = []
            for _ in range(20):
                start = time.monotonic()
                self.api.status([])
                times.append(time.monotonic() - start)
                time.sleep(0.02)
            seen["slowest"] = max(times)
            seen["flood"] = self.api.status(["flood"])["data"][0]["state"]

        self.assertEqual(self.run_with(body), 0)
        self.assertTrue(seen["reload"]["ok"], seen["reload"])
        self.assertIn(seen["flood"], ("STARTING", "RUNNING"))
        self.assertLess(seen["slowest"], 0.5)

    def test_calls_after_run_raise_backend_error(self) -> None:
        self.assertEqual(self.run_with(lambda: None), 0)
        calls = {
            "status": lambda: self.api.status([]),
            "start": lambda: self.api.start(["sleeper"]),
            "stop": lambda: self.api.stop(["sleeper"]),
            "restart": lambda: self.api.restart(["sleeper"]),
            "reload": self.api.reload,
            "shutdown": self.api.shutdown,
        }
        for name, call in calls.items():
            with self.subTest(call=name), self.assertRaises(BackendError):
                call()

    def test_missing_config(self) -> None:
        with self.assertRaises(ConfigError):
            Api("missing.yaml", interactive=False)
