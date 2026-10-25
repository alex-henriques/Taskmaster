from __future__ import annotations

import logging
import os
import selectors
import shlex
import tempfile
import time
import unittest

import yaml

from taskmaster.core.config import ProgramConfig, load
from taskmaster.core.reload import Plan, diff
from taskmaster.core.supervisor import Supervisor
from taskmaster.protocol import BAD_CONFIG, BUSY

LOG = logging.getLogger("test_reload")
LOG.addHandler(logging.NullHandler())

SLEEP = ("sleep", "30")
SLEEPER = {"cmd": "sleep 30", "starttime": 0.1}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUBBORN = {
    "cmd": shlex.quote(os.path.join(ROOT, "demo", "programs", "stubborn.sh")),
    "starttime": 0.1,
    "stoptime": 1,
}


class DiffTest(unittest.TestCase):
    def test_diff(self) -> None:
        a, b, c, d = (ProgramConfig(name, SLEEP) for name in "abcd")
        b2 = ProgramConfig("b", ("sleep", "31"))
        plan = diff({"a": a, "b": b, "c": c}, {"b": b2, "c": c, "d": d})
        self.assertEqual(plan, Plan(added=("d",), changed=("b",), removed=("a",)))

    def test_names_sorted(self) -> None:
        old = {name: ProgramConfig(name, SLEEP) for name in "edcba"}
        new = {name: ProgramConfig(name, ("sleep", "31")) for name in "edcba"}
        self.assertEqual(diff({}, old).added, ("a", "b", "c", "d", "e"))
        self.assertEqual(diff(old, new).changed, ("a", "b", "c", "d", "e"))
        self.assertEqual(diff(old, {}).removed, ("a", "b", "c", "d", "e"))


class ReloadTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "taskmaster.yaml")
        self.sel = selectors.DefaultSelector()
        self.addCleanup(self.sel.close)

    def write(self, **programs: dict) -> None:
        """Write the config file, one keyword per program."""
        with open(self.path, "w") as f:
            yaml.safe_dump({"programs": programs}, f)

    def make(self, **programs: dict) -> Supervisor:
        """A Supervisor on a file holding programs, after autostart."""
        self.write(**programs)
        sup = Supervisor(self.path, load(self.path), LOG, self.sel)
        self.addCleanup(self.shut_down, sup)
        sup.autostart()
        return sup

    def rows(self, sup: Supervisor) -> list[tuple[str, str]]:
        """(name, state) per instance, in the order status returns them."""
        return [(r["name"], r["state"]) for r in sup.status([])["data"]]

    def pids(self, sup: Supervisor) -> dict[str, int | None]:
        return {r["name"]: r["pid"] for r in sup.status([])["data"]}

    def drive(self, sup: Supervisor, until, timeout: float = 5.0) -> None:
        """Call reap() and tick() as the loop would, until until() is true."""
        end = time.monotonic() + timeout
        while not until():
            if time.monotonic() > end:
                self.fail(f"timed out: {self.rows(sup)}")
            sup.reap()
            sup.tick(time.monotonic())
            time.sleep(0.02)

    def running(self, sup: Supervisor, *names: str) -> None:
        """Drive until the instances are exactly names, all RUNNING."""
        self.drive(sup, lambda: self.rows(sup) == [(n, "RUNNING") for n in names])

    def shut_down(self, sup: Supervisor) -> None:
        sup.shutdown()
        self.drive(sup, lambda: sup.finished)

    def test_invalid_file_changes_nothing(self) -> None:
        sup = self.make(a=SLEEPER)
        self.running(sup, "a")
        before = self.pids(sup)
        self.write(a={**SLEEPER, "stoptim": 1}, b=SLEEPER)
        with self.assertLogs(LOG, logging.ERROR):
            reply = sup.reload()
        self.assertEqual(reply["error"], BAD_CONFIG)
        self.assertIn("programs.a.stoptim", reply["msg"])
        self.assertEqual(self.pids(sup), before)

    def test_same_file_changes_nothing(self) -> None:
        sup = self.make(a=SLEEPER)
        self.running(sup, "a")
        before = self.pids(sup)
        with self.assertLogs(LOG, logging.INFO) as logs:
            reply = sup.reload()
        self.assertEqual(
            reply, {"ok": True, "data": {"added": [], "changed": [], "removed": []}}
        )
        self.assertIn(
            "INFO:test_reload:reload: added [], changed [], removed []", logs.output
        )
        self.assertEqual(self.pids(sup), before)

    def test_second_reload_compares_with_the_first(self) -> None:
        idle = {**SLEEPER, "autostart": False}
        sup = self.make(a=idle)
        self.write(a=idle, b=idle)
        self.assertEqual(sup.reload()["data"]["added"], ["b"])
        self.assertEqual(sup.reload()["data"]["added"], [])

    def test_added(self) -> None:
        sup = self.make(a=SLEEPER)
        self.running(sup, "a")
        self.write(a=SLEEPER, off={**SLEEPER, "autostart": False}, on=SLEEPER)
        self.assertEqual(sup.reload()["data"]["added"], ["off", "on"])
        self.assertEqual(
            self.rows(sup), [("a", "RUNNING"), ("off", "STOPPED"), ("on", "STARTING")]
        )

    def test_removed_is_stopped_then_gone(self) -> None:
        sup = self.make(a=SLEEPER, b=SLEEPER)
        self.running(sup, "a", "b")
        old = self.pids(sup)["b"]
        self.write(a=SLEEPER)
        self.assertEqual(sup.reload()["data"]["removed"], ["b"])
        self.assertIn(dict(self.rows(sup))["b"], ("STOPPING", "STOPPED"))
        self.running(sup, "a")
        self.assertRaises(ProcessLookupError, os.kill, old, 0)

    def test_changed_cmd_starts_a_new_instance(self) -> None:
        sup = self.make(x=SLEEPER)
        self.running(sup, "x")
        old = self.pids(sup)["x"]
        self.write(x={**SLEEPER, "cmd": "sleep 31"})
        self.assertEqual(sup.reload()["data"]["changed"], ["x"])
        self.running(sup, "x")
        new = self.pids(sup)["x"]
        self.assertNotEqual(new, old)
        self.assertRaises(ProcessLookupError, os.kill, old, 0)
        with open(f"/proc/{new}/cmdline", "rb") as f:
            self.assertEqual(f.read(), b"sleep\x0031\x00")

    def test_unchanged_keeps_its_pid(self) -> None:
        sup = self.make(a=SLEEPER, b=SLEEPER)
        self.running(sup, "a", "b")
        before = self.pids(sup)
        self.write(a=SLEEPER, b={**SLEEPER, "cmd": "sleep 31"})
        self.assertEqual(sup.reload()["data"]["changed"], ["b"])
        self.running(sup, "a", "b")
        after = self.pids(sup)
        self.assertEqual(after["a"], before["a"])
        self.assertNotEqual(after["b"], before["b"])

    def test_unchanged_fatal_stays_fatal(self) -> None:
        fails = {"cmd": "false", "startretries": 0}
        sup = self.make(f=fails)
        self.drive(sup, lambda: self.rows(sup) == [("f", "FATAL")])
        before = sup.status(["f"])["data"]
        self.write(f=fails, n={**SLEEPER, "autostart": False})
        self.assertEqual(sup.reload()["data"]["added"], ["n"])
        sup.tick(time.monotonic())
        self.assertEqual(sup.status(["f"])["data"], before)

    def test_numprocs_2_to_3(self) -> None:
        sup = self.make(x={**SLEEPER, "numprocs": 2})
        self.running(sup, "x_00", "x_01")
        before = set(self.pids(sup).values())
        self.write(x={**SLEEPER, "numprocs": 3})
        self.assertEqual(sup.reload()["data"]["changed"], ["x"])
        self.running(sup, "x_00", "x_01", "x_02")
        self.assertFalse(before & set(self.pids(sup).values()))

    def test_slow_stop_and_a_second_reload(self) -> None:
        sup = self.make(s=STUBBORN)
        self.running(sup, "s")
        old = self.pids(sup)["s"]
        began = time.monotonic()
        self.write(s={**STUBBORN, "autostart": False})
        self.assertEqual(sup.reload()["data"]["changed"], ["s"])
        self.assertEqual(self.rows(sup), [("s", "STOPPING")])
        self.write(s={**STUBBORN, "env": {"RELOAD": 2}})
        self.assertEqual(sup.reload()["data"]["changed"], ["s"])
        self.running(sup, "s")
        self.assertGreaterEqual(time.monotonic() - began, 1)
        self.assertRaises(ProcessLookupError, os.kill, old, 0)

    def test_start_and_restart_busy_while_pending(self) -> None:
        sup = self.make(x={**STUBBORN, "numprocs": 2, "autostart": False})
        sup.start(["x_00"])
        self.drive(
            sup, lambda: self.rows(sup) == [("x_00", "RUNNING"), ("x_01", "STOPPED")]
        )
        self.write(x={**STUBBORN, "numprocs": 2})
        self.assertEqual(sup.reload()["data"]["changed"], ["x"])
        self.assertEqual(self.rows(sup), [("x_00", "STOPPING"), ("x_01", "STOPPED")])
        for reply in (sup.start(["x"]), sup.restart(["x"])):
            self.assertEqual([r.get("error") for r in reply["data"]], [BUSY, BUSY])
        self.running(sup, "x_00", "x_01")

    def test_added_back_while_still_stopping(self) -> None:
        sup = self.make(s=STUBBORN)
        self.running(sup, "s")
        old = self.pids(sup)["s"]
        self.write()
        self.assertEqual(sup.reload()["data"]["removed"], ["s"])
        self.write(s=STUBBORN)
        self.assertEqual(sup.reload()["data"]["added"], ["s"])
        self.assertEqual(self.pids(sup), {"s": old})
        self.running(sup, "s")
        self.assertRaises(ProcessLookupError, os.kill, old, 0)

    def test_new_instance_waits_for_its_name(self) -> None:
        sup = self.make(web_00=STUBBORN)
        self.running(sup, "web_00")
        old = self.pids(sup)["web_00"]
        self.write(web={**STUBBORN, "numprocs": 2})
        self.assertEqual(
            sup.reload()["data"],
            {"added": ["web"], "changed": [], "removed": ["web_00"]},
        )
        self.assertEqual(self.pids(sup), {"web_00": old})
        self.running(sup, "web_00", "web_01")
        self.assertRaises(ProcessLookupError, os.kill, old, 0)

    def test_shutdown_during_a_pending_reload(self) -> None:
        sup = self.make(s=STUBBORN)
        self.running(sup, "s")
        self.write(s={**STUBBORN, "env": {"RELOAD": 2}})
        self.assertEqual(sup.reload()["data"]["changed"], ["s"])
        sup.shutdown()
        self.drive(sup, lambda: sup.finished)
        self.assertEqual(self.rows(sup), [("s", "STOPPED")])
