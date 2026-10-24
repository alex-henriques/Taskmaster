from __future__ import annotations

import os
import pty
import select
import signal
import sys
import time
import traceback
import unittest

from taskmaster.protocol import BackendError
from taskmaster.shell import commands, repl
from tests.fakes import FakeBackend


class GoneBackend(FakeBackend):
    def status(self, names: list[str]) -> dict:
        raise BackendError("gone")


class GoneOnShutdown(FakeBackend):
    def shutdown(self) -> dict:
        raise BackendError("gone")


class CandidatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()

    def test_command_names(self) -> None:
        self.assertEqual(
            repl.candidates(self.backend, "", "st"), ["start", "status", "stop"]
        )
        self.assertEqual(
            repl.candidates(self.backend, "", ""), sorted(commands.COMMANDS)
        )
        self.assertEqual(self.backend.calls, [])  # no need to ask the backend

    def test_program_and_instance_names(self) -> None:
        self.assertEqual(
            repl.candidates(self.backend, "stop ", "vo"), ["vogsphere", "vogsphere_00"]
        )
        self.assertEqual(repl.candidates(self.backend, "start ", "a"), ["all"])
        self.assertEqual(repl.candidates(self.backend, "status ", "n"), ["nginx"])

    def test_names_are_asked_when_tab_is_pressed(self) -> None:
        repl.candidates(self.backend, "stop ", "")
        repl.candidates(self.backend, "stop ", "")
        self.assertEqual(self.backend.calls, [("status", []), ("status", [])])

    def test_second_name_is_completed_too(self) -> None:
        self.assertEqual(repl.candidates(self.backend, "stop nginx ", "br"), ["broken"])

    def test_nothing_for_commands_without_names(self) -> None:
        for before in ("reload ", "shutdown ", "quit ", "help ", "bogus "):
            with self.subTest(before=before):
                self.assertEqual(repl.candidates(self.backend, before, ""), [])


class ReplTest(unittest.TestCase):
    def spawn(self, backend: FakeBackend, shutdown_on_quit: bool = False) -> None:
        """Run repl.run in a child on a pty. When it returns, the child prints its calls."""
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            pid, fd = pty.fork()
        except OSError as e:
            self.skipTest(f"needs a pty: {e}")
        if pid == 0:
            code = 1
            try:
                sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
                repl.run(backend, shutdown_on_quit=shutdown_on_quit)
                print("calls:", [c[0] for c in backend.calls], flush=True)
                code = 0
            except BaseException:
                traceback.print_exc()
                raise
            finally:
                os._exit(code)
        self.pid, self.fd, self.out = pid, fd, b""
        self.addCleanup(self.reap)

    def reap(self) -> None:
        if self.pid:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)

    def read_until(self, done, timeout: float = 5.0) -> str:
        """Read the pty until done(text) is true. Fail on timeout or on EOF."""
        end = time.monotonic() + timeout
        while not done(self.out.decode(errors="replace")):
            left = end - time.monotonic()
            if left <= 0:
                self.fail(f"timeout, output so far: {self.out!r}")
            if select.select([self.fd], [], [], left)[0]:
                try:
                    chunk = os.read(self.fd, 1024)
                except OSError:  # EIO on Linux once the child is gone
                    chunk = b""
                if not chunk:
                    self.fail(f"child exited, output so far: {self.out!r}")
                self.out += chunk
        return self.out.decode(errors="replace")

    def prompts(self, n: int) -> None:
        self.read_until(lambda text: text.count(repl.PROMPT) >= n)

    def type(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def finish(self) -> str:
        """Wait for run to return and the child to exit cleanly. Return all the output."""
        text = self.read_until(lambda text: "calls:" in text and text.endswith("\n"))
        _, status = os.waitpid(self.pid, 0)
        self.pid = 0
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, text)
        return text

    def test_status_then_quit(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        self.type(b"status\r")
        self.prompts(2)
        self.assertIn("nginx" + " " * 28 + "RUNNING   pid 4242", self.out.decode())
        self.type(b"quit\r")
        self.assertIn("calls: ['status']", self.finish())

    def test_ctrl_c_gives_a_new_prompt(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        self.type(b"sta")
        self.read_until(lambda text: text.endswith("sta"))
        time.sleep(0.1)
        self.type(b"\x03")
        self.prompts(2)
        self.type(b"quit\r")
        self.assertIn("calls: []", self.finish())

    def test_ctrl_d_with_shutdown(self) -> None:
        self.spawn(FakeBackend(), shutdown_on_quit=True)
        self.prompts(1)
        self.type(b"\x04")
        text = self.finish()
        self.assertIn("shutting down", text)
        self.assertIn("calls: ['shutdown']", text)

    def test_ctrl_d_without_shutdown(self) -> None:
        self.spawn(FakeBackend(), shutdown_on_quit=False)
        self.prompts(1)
        self.type(b"\x04")
        self.assertIn("calls: []", self.finish())

    def test_backend_gone(self) -> None:
        self.spawn(GoneBackend(), shutdown_on_quit=True)
        self.prompts(1)
        self.type(b"status\r")
        text = self.finish()
        self.assertIn("gone", text)
        self.assertNotIn("shutdown", text)

    def test_backend_gone_on_shutdown(self) -> None:
        self.spawn(GoneOnShutdown(), shutdown_on_quit=True)
        self.prompts(1)
        self.type(b"quit\r")
        self.assertIn("gone", self.finish())

    def test_bad_input_does_not_crash(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        for n, line in enumerate(
            (b"nope", b"start", b"reload x", b"", b"'", b"st\xffatus"), 2
        ):
            self.type(line + b"\r")
            self.prompts(n)
        self.type(b"quit\r")
        self.finish()

    def test_tab_completes_commands_and_names(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        self.type(b"st\t\t")  # ambiguous: the second Tab lists the choices
        self.read_until(lambda text: "start" in text and "stop" in text)
        self.type(b"\x15stop vo\t")  # Ctrl-U, then the common prefix is completed
        self.read_until(
            lambda text: text.replace("\x07", "").endswith("stop vogsphere")
        )
        self.type(b"_\t")
        self.read_until(lambda text: text.replace("\x07", "").endswith("vogsphere_00"))
        self.type(b"\r")
        self.read_until(lambda text: "vogsphere_00: ERROR (not running)" in text)
        self.type(b"quit\r")
        self.finish()

    def test_tab_completes_names_with_a_dash(self) -> None:
        row = {
            "name": "web-app",
            "state": "RUNNING",
            "pid": 1,
            "since": 0.0,
            "exit": None,
            "error": None,
        }
        self.spawn(FakeBackend([row]))
        self.prompts(1)
        self.type(b"stop web-a\t")  # "-" must not split the word being completed
        self.read_until(lambda text: text.endswith("stop web-app"))
        self.type(b"\x15quit\r")
        self.finish()


if __name__ == "__main__":
    unittest.main()
