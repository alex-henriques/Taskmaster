from __future__ import annotations

import unittest

from taskmaster.shell.render import results, status_table


def row(name: str, state: str, **fields) -> dict:
    base = {"name": name, "state": state, "pid": None, "since": 0.0}
    return {**base, "exit": None, "error": None, **fields}


class StatusTableTest(unittest.TestCase):
    def test_uptime_from_since(self) -> None:
        now = 1000.0
        text = status_table([row("nginx", "RUNNING", pid=4242, since=now - 62)], now)
        self.assertEqual(text, "nginx  RUNNING  pid 4242, uptime 0:01:02")

    def test_columns_are_aligned(self) -> None:
        now = 1000.0
        text = status_table(
            [
                row("a", "STARTING", pid=7, since=now - 1),
                row("vogsphere_00", "EXITED", exit=3),
                row("web", "STOPPED", exit="SIGKILL"),
                row("broken", "FATAL", error="can't find command 'nope'"),
                row("idle", "STOPPED"),
            ],
            now,
        )
        self.assertEqual(
            text.splitlines(),
            [
                "a             STARTING  pid 7, uptime 0:00:01",
                "vogsphere_00  EXITED    exit 3",
                "web           STOPPED   SIGKILL",
                "broken        FATAL     can't find command 'nope'",
                "idle          STOPPED",
            ],
        )

    def test_days(self) -> None:
        text = status_table([row("a", "RUNNING", pid=1, since=0.0)], 86400 + 62)
        self.assertTrue(text.endswith("uptime 1 day, 0:01:02"))

    def test_no_rows(self) -> None:
        self.assertEqual(status_table([], 0.0), "")


class ResultsTest(unittest.TestCase):
    def test_per_instance(self) -> None:
        reply = {
            "ok": False,
            "data": [
                {"name": "nginx", "ok": True, "msg": "starting"},
                {
                    "name": "vogsphere_00",
                    "ok": False,
                    "error": "ALREADY_STARTED",
                    "msg": "already started",
                },
            ],
        }
        self.assertEqual(
            results(reply), "nginx: starting\nvogsphere_00: ERROR (already started)"
        )

    def test_reload(self) -> None:
        reply = {
            "ok": True,
            "data": {"added": ["a", "b"], "changed": [], "removed": ["c"]},
        }
        self.assertEqual(results(reply), "added: a, b, changed: none, removed: c")

    def test_shutdown(self) -> None:
        self.assertEqual(results({"ok": True, "msg": "shutting down"}), "shutting down")

    def test_whole_request_failed(self) -> None:
        reply = {"ok": False, "error": "BAD_CONFIG", "msg": "programs.a.cmd: required"}
        self.assertEqual(results(reply), "ERROR (programs.a.cmd: required)")
