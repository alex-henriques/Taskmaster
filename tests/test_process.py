from __future__ import annotations

import logging
import os
import selectors
import signal
import subprocess
import tempfile
import time
import unittest

from taskmaster.core.config import ProgramConfig
from taskmaster.core.process import Process, State
from taskmaster.protocol import ALREADY_STARTED, BUSY, NOT_RUNNING

LOG = logging.getLogger("test_process")
LOG.addHandler(logging.NullHandler())

IGNORES_TERM = 'trap "" TERM; exec sleep 30'


class ProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.sel = selectors.DefaultSelector()
        self.addCleanup(self.sel.close)

    def make(self, *cmd: str, **fields) -> Process:
        p = Process("t", ProgramConfig("t", cmd, **fields), LOG, self.sel)
        self.addCleanup(self.reap, p)
        return p

    def drive(self, p: Process, until, timeout: float = 5.0) -> None:
        """Call poll() and on_deadline() as the loop would, until until(p) is true."""
        end = time.monotonic() + timeout
        while not until(p):
            if time.monotonic() > end:
                self.fail(f"timed out in {p.state}")
            p.poll()
            p.on_deadline(time.monotonic())
            time.sleep(0.02)

    def wait_state(self, p: Process, state: State, timeout: float = 5.0) -> None:
        self.drive(p, lambda p: p.state is state, timeout)

    def reap(self, p: Process) -> None:
        p.stop()
        self.drive(p, lambda p: not p.is_alive())

    @classmethod
    def tearDownClass(cls) -> None:
        ps = subprocess.run(
            ["ps", "-o", "stat=", "--ppid", str(os.getpid())],
            capture_output=True,
            text=True,
            check=True,
        )
        if "Z" in ps.stdout:
            raise AssertionError(f"zombie children left:\n{ps.stdout}")

    def test_starting_then_running(self) -> None:
        p = self.make("sleep", "30", starttime=0.2)
        t0 = time.monotonic()
        p.start()
        self.assertIs(p.state, State.STARTING)
        self.wait_state(p, State.RUNNING)
        self.assertGreaterEqual(time.monotonic() - t0, 0.2)

    def test_retries_then_fatal(self) -> None:
        p = self.make("sh", "-c", "exit 1", startretries=2)
        t0 = time.monotonic()
        with self.assertLogs(LOG) as logs:
            p.start()
            self.wait_state(p, State.FATAL, timeout=10)
        spawns = [line for line in logs.output if "-> STARTING" in line]
        self.assertEqual(len(spawns), 3)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 3)
        self.assertLess(elapsed, 4)

    def test_command_not_found(self) -> None:
        p = self.make("/nonexistent/command", startretries=0)
        p.start()
        self.assertIs(p.state, State.FATAL)
        self.assertIsNotNone(p.info()["error"])

    def test_autorestart(self) -> None:
        cases = [
            ("unexpected", 0, State.EXITED),
            ("unexpected", 3, State.STARTING),
            ("always", 0, State.STARTING),
            ("never", 3, State.EXITED),
        ]
        for policy, code, after in cases:
            with self.subTest(autorestart=policy, code=code):
                p = self.make(
                    "sh",
                    "-c",
                    f"sleep 0.5; exit {code}",
                    starttime=0.1,
                    autorestart=policy,
                )
                p.start()
                self.drive(p, lambda p: p.exit is not None)
                self.assertIs(p.state, after)

    def test_killed_from_outside(self) -> None:
        for sig, name in ((signal.SIGKILL, "SIGKILL"), (40, "SIG40")):
            with self.subTest(name):
                p = self.make("sleep", "30", starttime=0.1)
                p.start()
                self.wait_state(p, State.RUNNING)
                with self.assertLogs(LOG, logging.WARNING):
                    os.kill(p.info()["pid"], sig)
                    self.drive(p, lambda p: p.exit is not None)
                self.assertEqual(p.info()["exit"], name)

    def test_sigkill_after_stoptime(self) -> None:
        p = self.make("sh", "-c", IGNORES_TERM, starttime=0.1, stoptime=0.5)
        p.start()
        self.wait_state(p, State.RUNNING)
        with self.assertLogs(LOG, logging.WARNING) as logs:
            p.stop()
            self.assertIs(p.state, State.STOPPING)
            self.wait_state(p, State.STOPPED)
        self.assertTrue(any("killed after stoptime" in line for line in logs.output))

    def test_state_command_table(self) -> None:
        running = self.make("sleep", "30", starttime=0.1)
        running.start()
        self.wait_state(running, State.RUNNING)
        self.assertEqual(running.start()["error"], ALREADY_STARTED)
        running.stop()
        self.assertEqual(running.start()["error"], BUSY)

        self.assertEqual(self.make("sleep", "30").stop()["error"], NOT_RUNNING)

        backoff = self.make("sh", "-c", "exit 1")
        backoff.start()
        self.wait_state(backoff, State.BACKOFF)
        self.assertTrue(backoff.stop()["ok"])
        self.assertIs(backoff.state, State.STOPPED)

    def test_restart(self) -> None:
        p = self.make("sleep", "30", starttime=0.1)
        p.start()
        self.wait_state(p, State.RUNNING)
        old = p.info()["pid"]
        p.restart()
        self.assertIs(p.state, State.STOPPING)
        self.wait_state(p, State.RUNNING)
        self.assertNotEqual(p.info()["pid"], old)
        p.restart()
        p.stop()
        self.drive(p, lambda p: not p.is_alive())
        self.assertIs(p.state, State.STOPPED)

    def test_child_environment(self) -> None:
        out = os.path.join(self.dir, "out")
        p = self.make(
            "sh",
            "-c",
            "umask; pwd; echo $ANSWER",
            umask=0o27,
            workingdir=self.dir,
            env=(("ANSWER", "42"),),
            stdout=out,
        )
        p.start()
        self.drive(p, lambda p: not p.is_alive())
        with open(out) as f:
            self.assertEqual(
                f.read().split(), ["0027", os.path.realpath(self.dir), "42"]
            )
