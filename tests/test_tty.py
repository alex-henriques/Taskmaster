from __future__ import annotations

import logging
import os
import selectors
import tempfile
import time
import unittest

from taskmaster.core.config import ProgramConfig
from taskmaster.core.process import Process, State

LOG = logging.getLogger("test_tty")
LOG.addHandler(logging.NullHandler())


class FakeSink:
    """Keeps what the core sends, as server.Connection would get it."""

    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def send_raw(self, data: bytes) -> None:
        self.data += data

    def close(self) -> None:
        self.closed = True


class ConnectionLikeSink(FakeSink):
    """Like server.Connection: closing it detaches it, and once full it closes itself."""

    def __init__(self, process: Process) -> None:
        super().__init__()
        self.process = process
        self.full = False

    def send_raw(self, data: bytes) -> None:
        if self.full:
            self.close()
        else:
            super().send_raw(data)

    def close(self) -> None:
        super().close()
        self.process.detach(self)


class TtyTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.out = os.path.join(self.dir, "t.out")
        self.sel = selectors.DefaultSelector()
        self.addCleanup(self.sel.close)

    def make(self, *cmd: str, **fields) -> Process:
        fields = {"tty": True, "stdout": self.out, **fields}
        p = Process("t", ProgramConfig("t", cmd, **fields), LOG, self.sel)
        self.addCleanup(self.reap, p)
        return p

    def drive(self, p: Process, until, timeout: float = 5.0, pump: bool = True) -> None:
        """Do what the loop does (pty reads, poll, deadlines) until until(p) is true.

        pump=False skips the pty reads, so poll() sees a death before the EIO does.
        """
        end = time.monotonic() + timeout
        while not until(p):
            if time.monotonic() > end:
                self.fail(f"timed out in {p.state}")
            if pump:
                for key, mask in self.sel.select(0.02):
                    key.data(key.fileobj, mask)
            else:
                time.sleep(0.02)
            p.poll()
            p.on_deadline(time.monotonic())

    def reap(self, p: Process) -> None:
        p.stop()
        self.drive(p, lambda p: not p.is_alive())

    def output(self) -> bytes:
        with open(self.out, "rb") as f:
            return f.read()

    def test_sees_a_tty(self) -> None:
        p = self.make("sh", "-c", "[ -t 0 ] && echo IS_TTY; exec sleep 30")
        p.start()
        self.drive(p, lambda p: b"IS_TTY" in self.output())

    def test_has_a_controlling_terminal(self) -> None:
        p = self.make("sh", "-c", "echo READY; exec sleep 30")
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        with open(f"/proc/{p.info()['pid']}/stat") as f:
            stat = f.read()
        self.assertNotEqual(stat.rsplit(")", 1)[1].split()[4], "0")

    def test_ctrl_c_reaches_the_program(self) -> None:
        p = self.make(
            "sh", "-c", "trap 'exit 3' INT; echo READY; while :; do sleep 0.1; done"
        )
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        p.write_input(b"\x03")
        self.drive(p, lambda p: p.info()["exit"] is not None)
        self.assertEqual(p.info()["exit"], 3)

    def test_input_that_does_not_fit_is_dropped(self) -> None:
        p = self.make("sleep", "30")
        p.start()
        for _ in range(10):
            p.write_input(b"x" * 100_000)

    def test_window_size(self) -> None:
        script = "trap 'stty size' WINCH; stty size; while :; do sleep 0.1; done"
        p = self.make("sh", "-c", script)
        p.start()
        self.drive(p, lambda p: b"24 80" in self.output())
        p.attach(FakeSink(), 40, 120)
        self.drive(p, lambda p: b"40 120" in self.output())

    def test_every_attached_sink_gets_the_output(self) -> None:
        p = self.make(
            "sh", "-c", "echo READY; read line; echo got $line; exec sleep 30"
        )
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        first, second = FakeSink(), FakeSink()
        p.attach(first, 24, 80)
        p.attach(second, 24, 80)
        p.write_input(b"hi\n")
        self.drive(p, lambda p: b"got hi" in first.data and b"got hi" in second.data)

    def test_attach_replays_the_last_4_kib(self) -> None:
        script = "head -c 5000 /dev/zero | tr '\\0' a; printf Z; exec sleep 30"
        p = self.make("sh", "-c", script)
        p.start()
        self.drive(p, lambda p: self.output().endswith(b"Z"))
        sink = FakeSink()
        p.attach(sink, 24, 80)
        self.assertEqual(sink.data, b"a" * 4095 + b"Z")

    def test_a_respawn_leaves_a_single_pty(self) -> None:
        p = self.make("sleep", "0.2", starttime=0.1, autorestart="always")
        p.start()
        pids = set()

        def respawned_3_times(p: Process) -> bool:
            pids.add(p.info()["pid"])
            return len(pids) > 3

        self.drive(p, respawned_3_times, pump=False)
        self.assertEqual(len(self.sel.get_map()), 1)

    def test_output_written_just_before_death_is_kept(self) -> None:
        p = self.make("sh", "-c", "echo LAST_WORDS", startretries=0)
        p.start()
        self.drive(p, lambda p: not p.is_alive(), pump=False)
        self.assertIn(b"LAST_WORDS", self.output())

    def test_death_closes_the_sinks(self) -> None:
        p = self.make("sh", "-c", "echo READY; exec sleep 30")
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        sink = FakeSink()
        p.attach(sink, 24, 80)
        p.stop()
        self.drive(p, lambda p: not p.is_alive())
        self.assertTrue(sink.closed)

    def test_input_after_death_is_ignored(self) -> None:
        p = self.make("true", startretries=0)
        p.start()
        self.drive(p, lambda p: p.state is State.FATAL)
        p.write_input(b"too late\n")

    def test_a_detached_sink_gets_nothing_more(self) -> None:
        p = self.make(
            "sh", "-c", "echo READY; read line; echo got $line; exec sleep 30"
        )
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        gone, staying = FakeSink(), FakeSink()
        p.attach(gone, 24, 80)
        p.attach(staying, 24, 80)
        p.detach(gone)
        p.write_input(b"hi\n")
        self.drive(p, lambda p: b"got hi" in staying.data)
        self.assertNotIn(b"got hi", gone.data)

    def test_a_sink_may_detach_while_it_gets_output(self) -> None:
        p = self.make(
            "sh", "-c", "echo READY; read line; echo got $line; exec sleep 30"
        )
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        full, staying = ConnectionLikeSink(p), FakeSink()
        p.attach(full, 24, 80)
        p.attach(staying, 24, 80)
        full.full = True
        p.write_input(b"hi\n")
        self.drive(p, lambda p: b"got hi" in staying.data)
        self.assertTrue(full.closed)

    def test_a_sink_may_detach_while_death_closes_it(self) -> None:
        p = self.make("sh", "-c", "echo READY; exec sleep 30")
        p.start()
        self.drive(p, lambda p: b"READY" in self.output())
        sink = ConnectionLikeSink(p)
        p.attach(sink, 24, 80)
        p.stop()
        self.drive(p, lambda p: not p.is_alive())
        self.assertTrue(sink.closed)

    def test_a_failed_spawn_leaves_no_pty(self) -> None:
        p = self.make("true", workingdir="/nonexistent", startretries=0)
        p.start()
        self.assertIs(p.state, State.FATAL)
        self.assertEqual(len(self.sel.get_map()), 0)

    def test_a_flood_does_not_hold_the_loop(self) -> None:
        p = self.make("yes", stdout=None, starttime=0.5, stoptime=1)
        p.start()
        self.drive(p, lambda p: p.state is State.RUNNING)
        p.stop()
        self.drive(p, lambda p: p.state is State.STOPPED, timeout=1)
