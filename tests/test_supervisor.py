from __future__ import annotations

import logging
import os
import selectors
import signal
import subprocess
import time
import unittest

from taskmaster.core.config import ProgramConfig
from taskmaster.core.supervisor import Supervisor
from taskmaster.protocol import BAD_NAME

LOG = logging.getLogger("test_supervisor")
LOG.addHandler(logging.NullHandler())

SLEEP = ("sleep", "30")


class SupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sel = selectors.DefaultSelector()
        self.addCleanup(self.sel.close)

    def make(self, *configs: ProgramConfig) -> Supervisor:
        sup = Supervisor("unused.yaml", {c.name: c for c in configs}, LOG, self.sel)
        self.addCleanup(self.stop_all, sup)
        return sup

    def rows(self, sup: Supervisor) -> list[tuple[str, str]]:
        """(name, state) per instance, in the order status returns them."""
        return [(r["name"], r["state"]) for r in sup.status([])["data"]]

    def drive(self, sup: Supervisor, until, timeout: float = 5.0) -> None:
        """Call reap() and tick() as the loop would, until until() is true."""
        end = time.monotonic() + timeout
        while not until():
            if time.monotonic() > end:
                self.fail(f"timed out: {self.rows(sup)}")
            sup.reap()
            sup.tick(time.monotonic())
            time.sleep(0.02)

    def stop_all(self, sup: Supervisor) -> None:
        sup.stop(["all"])
        self.drive(sup, lambda: all(r["pid"] is None for r in sup.status([])["data"]))

    def test_instance_names_sorted(self) -> None:
        # x before a: an unsorted status would list x_00 first
        sup = self.make(
            ProgramConfig("x", SLEEP, numprocs=3), ProgramConfig("a", SLEEP)
        )
        self.assertEqual(
            self.rows(sup),
            [
                ("a", "STOPPED"),
                ("x_00", "STOPPED"),
                ("x_01", "STOPPED"),
                ("x_02", "STOPPED"),
            ],
        )

    def test_start_program(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, numprocs=3))
        self.assertEqual(
            sup.start(["x"]),
            {
                "ok": True,
                "data": [
                    {"name": "x_00", "ok": True, "msg": "starting"},
                    {"name": "x_01", "ok": True, "msg": "starting"},
                    {"name": "x_02", "ok": True, "msg": "starting"},
                ],
            },
        )

    def test_start_one_instance(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, numprocs=3))
        sup.start(["x_01"])
        self.assertEqual(
            self.rows(sup),
            [("x_00", "STOPPED"), ("x_01", "STARTING"), ("x_02", "STOPPED")],
        )

    def test_bad_name_changes_nothing(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, starttime=0.1))
        sup.start(["x"])
        self.drive(sup, lambda: self.rows(sup) == [("x", "RUNNING")])
        self.assertEqual(sup.status(["nope"]).get("error"), BAD_NAME)
        self.assertEqual(sup.stop(["nope"]).get("error"), BAD_NAME)
        self.assertEqual(sup.stop(["x", "nope"]).get("error"), BAD_NAME)
        self.assertEqual(self.rows(sup), [("x", "RUNNING")])

    def test_duplicates_merged(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, numprocs=2))
        reply = sup.start(["x_01", "x", "x_01"])
        self.assertEqual([r["name"] for r in reply["data"]], ["x_00", "x_01"])

    def test_ok_only_if_all_ok(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, numprocs=2))
        sup.start(["x_01"])
        reply = sup.start(["x"])
        self.assertFalse(reply["ok"])
        self.assertEqual(
            [(r["name"], r["ok"]) for r in reply["data"]],
            [("x_00", True), ("x_01", False)],
        )

    def test_restart_runs_a_new_process(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, starttime=0.1))
        sup.start(["x"])
        self.drive(sup, lambda: self.rows(sup) == [("x", "RUNNING")])
        old_pid = sup.status([])["data"][0]["pid"]
        self.assertTrue(sup.restart(["x"])["ok"])
        self.drive(sup, lambda: self.rows(sup) == [("x", "RUNNING")])
        self.assertNotEqual(sup.status([])["data"][0]["pid"], old_pid)

    def test_tick_applies_deadlines(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, starttime=60))
        sup.start(["x"])
        sup.tick(time.monotonic())
        self.assertEqual(self.rows(sup), [("x", "STARTING")])
        sup.tick(time.monotonic() + 61)
        self.assertEqual(self.rows(sup), [("x", "RUNNING")])

    def test_time_to_next_deadline(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, starttime=5))
        self.assertIsNone(sup.time_to_next_deadline())
        sup.start(["x"])
        t = sup.time_to_next_deadline()
        self.assertGreater(t, 0)
        self.assertLessEqual(t, 5)
        sup.tick(time.monotonic() + 6)
        self.assertIsNone(sup.time_to_next_deadline())

    def test_overdue_deadline_is_zero(self) -> None:
        sup = self.make(ProgramConfig("x", SLEEP, starttime=0))
        sup.start(["x"])
        self.assertEqual(sup.time_to_next_deadline(), 0.0)

    def test_coalesced_deaths(self) -> None:
        sup = self.make(
            ProgramConfig("x", SLEEP, numprocs=8, starttime=0.1, autorestart="never")
        )
        sup.start(["x"])
        self.drive(sup, lambda: {s for _, s in self.rows(sup)} == {"RUNNING"})
        pids = [r["pid"] for r in sup.status([])["data"]]
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        for pid in pids:
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)  # dead, not reaped
        sup.reap()
        self.assertEqual(
            {(r["state"], r["exit"]) for r in sup.status([])["data"]},
            {("EXITED", "SIGKILL")},
        )
        ps = subprocess.run(
            ["ps", "-o", "stat=", "--ppid", str(os.getpid())],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertNotIn("Z", ps.stdout)

    def test_autostart(self) -> None:
        sup = self.make(
            ProgramConfig("on", SLEEP), ProgramConfig("off", SLEEP, autostart=False)
        )
        sup.autostart()
        self.assertEqual(self.rows(sup), [("off", "STOPPED"), ("on", "STARTING")])

    def test_status_by_name(self) -> None:
        sup = self.make(
            ProgramConfig("x", SLEEP, numprocs=2), ProgramConfig("a", SLEEP)
        )
        self.assertEqual(
            [r["name"] for r in sup.status(["x"])["data"]], ["x_00", "x_01"]
        )

    def test_all_with_no_programs(self) -> None:
        sup = self.make()
        self.assertEqual(sup.stop(["all"]), {"ok": True, "data": []})
