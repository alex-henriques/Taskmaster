from __future__ import annotations

import unittest

from taskmaster.protocol import Backend, BackendError
from taskmaster.shell.commands import COMMANDS, Result, execute
from tests.fakes import FakeBackend


class ExecuteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()

    def test_status_default_rows(self) -> None:
        result = execute("status", self.backend)
        self.assertTrue(result.ok)
        self.assertEqual(
            result.text.splitlines(),
            [
                "broken        FATAL    can't find command 'nope'",
                "nginx         RUNNING  pid 4242, uptime 0:01:02",
                "vogsphere_00  STOPPED",
            ],
        )
        self.assertEqual(self.backend.calls, [("status", [])])

    def test_bad_name(self) -> None:
        for verb in ("status", "start"):
            with self.subTest(verb):
                self.assertEqual(
                    execute(f"{verb} nope", self.backend),
                    Result("ERROR (no such process: nope)", ok=False),
                )

    def test_start_mixed(self) -> None:
        result = execute("start nginx vogsphere", self.backend)
        self.assertFalse(result.ok)
        self.assertEqual(
            result.text, "nginx: ERROR (already started)\nvogsphere_00: starting"
        )

    def test_stop_all(self) -> None:
        execute("stop all", self.backend)
        self.assertEqual(self.backend.calls, [("stop", ["all"])])

    def test_reload_and_shutdown(self) -> None:
        self.assertEqual(
            execute("reload", self.backend),
            Result("added: none, changed: none, removed: none"),
        )
        self.assertEqual(execute("shutdown", self.backend), Result("shutting down"))
        self.assertEqual(self.backend.calls, [("reload", ()), ("shutdown", ())])

    def test_wrong_number_of_arguments(self) -> None:
        self.assertEqual(
            execute("start", self.backend),
            Result("usage: start <name>... | all", ok=False),
        )
        self.assertFalse(execute("reload now", self.backend).ok)
        self.assertFalse(execute("quit now", self.backend).quit)
        self.assertEqual(self.backend.calls, [])

    def test_unknown_command(self) -> None:
        self.assertEqual(
            execute("frobnicate", self.backend),
            Result("*** Unknown syntax: frobnicate", ok=False),
        )

    def test_empty_lines(self) -> None:
        self.assertEqual(execute("", self.backend), Result(""))
        self.assertEqual(execute("   ", self.backend), Result(""))

    def test_help_lists_every_command(self) -> None:
        result = execute("help", self.backend)
        self.assertTrue(result.ok)
        for cmd in COMMANDS.values():
            self.assertIn(cmd.usage, result.text)
            self.assertIn(cmd.help, result.text)
        self.assertNotIn("attach", result.text)
        self.assertEqual(self.backend.calls, [])

    def test_help_one_command(self) -> None:
        c = COMMANDS["start"]
        self.assertEqual(
            execute("help start", self.backend), Result(f"{c.usage}\n   {c.help}")
        )

    def test_help_unknown_command(self) -> None:
        self.assertFalse(execute("help frobnicate", self.backend).ok)

    def test_quit(self) -> None:
        self.assertEqual(execute("quit", self.backend), Result("", quit=True))
        self.assertEqual(self.backend.calls, [])

    def test_unbalanced_quote(self) -> None:
        result = execute('stop "x', self.backend)
        self.assertFalse(result.ok)
        self.assertTrue(result.text)
        self.assertEqual(self.backend.calls, [])

    def test_table_matches_backend(self) -> None:
        # every command that is not shell only is a Backend method
        for name in COMMANDS:
            if name not in ("help", "quit"):
                self.assertTrue(hasattr(Backend, name), name)


def test_backend_error_propagates(self) -> None:
    class Gone(FakeBackend):
        def stop(self, names: list[str]) -> dict:
            raise BackendError("gone")

    with self.assertRaises(BackendError):
        execute("stop all", Gone())
