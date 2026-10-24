from __future__ import annotations

import contextlib
import os
import signal
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from taskmaster.core import logger
from taskmaster.core.config import ProgramConfig
from taskmaster.core.loop import Loop
from taskmaster.core.supervisor import Supervisor
from taskmaster.protocol import INTERNAL, BackendError

SLEEP = ("sleep", "30")


class LoopTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = os.path.join(tmp.name, "taskmaster.log")
        self.log = logger.setup(self.log_path)
        handler = self.log.handlers[-1]
        self.addCleanup(handler.close)
        self.addCleanup(self.log.removeHandler, handler)
        self.loop = Loop(self.log, interactive=True)

    def make(self, *configs: ProgramConfig) -> Supervisor:
        programs = {c.name: c for c in configs}
        return Supervisor("unused.yaml", programs, self.log, self.loop.sel)

    def log_text(self) -> str:
        with open(self.log_path) as f:
            return f.read()

    def wait_log(self, text: str, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while text not in self.log_text():
            if time.monotonic() > end:
                self.fail(f"{text!r} never logged:\n{self.log_text()}")
            time.sleep(0.02)

    def kill_when(self, text: str, sig: int = signal.SIGTERM):
        """A body for run_with: wait until text is logged, then send sig to taskmaster."""

        def body() -> None:
            self.wait_log(text)
            os.kill(os.getpid(), sig)

        return body

    def run_with(self, sup: Supervisor, body) -> int:
        """Run the loop on this thread while body() runs on a helper thread."""

        def helper() -> None:
            try:
                self.loop.submit(lambda: None).result()
                body()
            finally:
                with contextlib.suppress(BackendError):
                    self.loop.submit(sup.shutdown)

        with ThreadPoolExecutor(1) as pool:
            done = pool.submit(helper)
            code = self.loop.run(sup)
        done.result()
        return code

    def test_fatal_without_any_command(self) -> None:
        cmd = ("sh", "-c", "exit 1")
        sup = self.make(ProgramConfig("bad", cmd, startretries=1, starttime=0.2))
        self.assertEqual(self.run_with(sup, self.kill_when("gave up")), 0)
        self.assertEqual(sup.status([])["data"][0]["state"], "FATAL")

    def test_sigterm_stops_children(self) -> None:
        sup = self.make(ProgramConfig("s", SLEEP, starttime=0.2))
        pids = []

        def body() -> None:
            self.wait_log("-> RUNNING")
            pids.append(self.loop.submit(sup.status, []).result()["data"][0]["pid"])
            os.kill(os.getpid(), signal.SIGTERM)

        self.assertEqual(self.run_with(sup, body), 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)
        self.assertTrue(self.log_text().splitlines()[-1].endswith("taskmaster stopped"))

    def test_sigkill_after_stoptime(self) -> None:
        cmd = ("sh", "-c", 'trap "" TERM; exec sleep 30')
        sup = self.make(ProgramConfig("t", cmd, starttime=0.3, stoptime=0.5))
        self.run_with(sup, self.kill_when("-> RUNNING"))
        self.assertIn("killed after stoptime", self.log_text())

    def test_stopsignal_int_reaches_child(self) -> None:
        config = ProgramConfig(
            "i", SLEEP, starttime=0.2, stopsignal=signal.SIGINT, stoptime=5
        )
        sup = self.make(config)
        sent = []

        def body() -> None:
            self.wait_log("-> RUNNING")
            sent.append(time.monotonic())
            os.kill(os.getpid(), signal.SIGTERM)

        self.run_with(sup, body)
        self.assertLess(time.monotonic() - sent[0], 1.0)
        self.assertNotIn("killed after stoptime", self.log_text())

    def test_sigquit_shuts_down(self) -> None:
        sup = self.make(ProgramConfig("q", SLEEP, starttime=0.2))
        body = self.kill_when("-> RUNNING", signal.SIGQUIT)
        self.assertEqual(self.run_with(sup, body), 0)
        self.assertIn("received SIGQUIT", self.log_text())

    def test_sigint_ignored_in_the_shell(self) -> None:
        sup = self.make()

        def body() -> None:
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.2)
            self.assertEqual(self.loop.submit(lambda: "alive").result(), "alive")
            self.assertNotIn("received SIGINT", self.log_text())

        self.run_with(sup, body)

    def test_sigint_shuts_down_the_daemon(self) -> None:
        self.loop = Loop(self.log, interactive=False)
        sup = self.make()
        body = self.kill_when("taskmaster started", signal.SIGINT)
        self.assertEqual(self.run_with(sup, body), 0)
        self.assertIn("received SIGINT", self.log_text())

    def test_submit(self) -> None:
        sup = self.make()
        replies = []

        def boom() -> None:
            raise RuntimeError("boom")

        def body() -> None:
            replies.append(self.loop.submit(lambda a, b: a + b, 40, 2).result())
            replies.append(self.loop.submit(boom).result())
            replies.append(self.loop.submit(sup.status, []).result())
            replies.append(self.loop.submit(self.loop.in_loop_thread).result())
            replies.append(self.loop.in_loop_thread())

        self.run_with(sup, body)
        failed = {"ok": False, "error": INTERNAL, "msg": "boom"}
        self.assertEqual(replies, [42, failed, {"ok": True, "data": []}, True, False])
        self.assertIn("ERROR error in a submitted call", self.log_text())
        self.assertIn("RuntimeError: boom", self.log_text())

    def test_cancelled_call_is_skipped(self) -> None:
        sup = self.make()
        ran = []
        cancelled = self.loop.submit(ran.append, "cancelled")
        cancelled.cancel()
        later = self.loop.submit(lambda: "later")
        self.run_with(sup, lambda: self.assertEqual(later.result(timeout=2), "later"))
        self.assertEqual(ran, [])

    def test_base_exception_in_a_call(self) -> None:
        sup = self.make()
        call = self.loop.submit(sys.exit, 3)
        with self.assertRaises(SystemExit):
            self.loop.run(sup)
        self.assertIsInstance(call.exception(timeout=0), BackendError)
        self.assertEqual(signal.set_wakeup_fd(-1), -1)

    def test_error_in_reap_keeps_the_loop_going(self) -> None:
        cmd = ("sh", "-c", "exit 1")
        sup = self.make(ProgramConfig("bad", cmd, startretries=1, starttime=0.2))
        reap = sup.reap

        def failing_reap() -> None:
            reap()
            raise RuntimeError("reap boom")

        sup.reap = failing_reap
        self.run_with(sup, self.kill_when("gave up"))
        self.assertIn("ERROR error in", self.log_text())
        self.assertIn("RuntimeError: reap boom", self.log_text())

    def test_error_in_tick_keeps_the_loop_going(self) -> None:
        sup = self.make()
        tick = sup.tick

        def failing_tick(now: float) -> None:
            tick(now)
            raise RuntimeError("tick boom")

        sup.tick = failing_tick
        self.run_with(
            sup, lambda: self.assertEqual(self.loop.submit(lambda: 1).result(), 1)
        )
        self.assertIn("ERROR error in", self.log_text())
        self.assertIn("RuntimeError: tick boom", self.log_text())

    def test_after_run(self) -> None:
        before = signal.getsignal(signal.SIGTERM)
        sup = self.make()
        sup.shutdown()
        queued = self.loop.submit(lambda: None)
        cancelled = self.loop.submit(lambda: None)
        cancelled.cancel()
        self.assertEqual(self.loop.run(sup), 0)
        self.assertIsInstance(queued.exception(timeout=0), BackendError)
        self.assertTrue(cancelled.cancelled())
        with self.assertRaises(BackendError):
            self.loop.submit(lambda: None)
        self.assertFalse(self.loop.in_loop_thread())
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual(signal.set_wakeup_fd(-1), -1)


if __name__ == "__main__":
    unittest.main()
