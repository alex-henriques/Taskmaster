from __future__ import annotations

import os
import select
import sys
import time
import unittest

from taskmaster.protocol import BackendError
from taskmaster.shell import repl
from tests.fakes import FakeBackend

try:
    import pty
except ImportError:
    pty = None


class GoneBackend(FakeBackend):
    def status(self, names: list[str]) -> dict:
        raise BackendError("gone")


@unittest.skipIf(pty is None or not hasattr(os, "fork"), "needs pty.fork")
class ReplTest(unittest.TestCase):
    def spawn(self, backend: FakeBackend, shutdown_on_quit: bool = False) -> None:
        """Run repl.run in a child on a pty. When it returns, the child prints its calls."""
        sys.stdout.flush()
        sys.stderr.flush()
        pid, fd = pty.fork()
        if pid == 0:
            code = 1  # never return into unittest in the child, even on an exception
            try:
                sys.stdin, sys.stdout, sys.stderr = (
                    sys.__stdin__,
                    sys.__stdout__,
                    sys.__stderr__,
                )
                repl.run(backend, shutdown_on_quit=shutdown_on_quit)
                print("calls:", [c[0] for c in backend.calls], flush=True)
                code = 0
            finally:
                os._exit(code)
        self.pid, self.fd, self.out = pid, fd, b""
        self.addCleanup(self.reap)

    def reap(self) -> None:
        if self.pid:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        os.close(self.fd)

    def read_until(self, done, timeout: float = 5.0) -> str:
        """Read the pty until done(text) is true. Fail on timeout."""
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
                    break
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
        self.assertIn("nginx         RUNNING  pid 4242", self.out.decode())
        self.type(b"quit\r")
        self.assertIn("calls: ['status']", self.finish())

    def test_ctrl_c_gives_a_new_prompt(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        self.type(b"sta\x03")
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

    def test_bad_input_does_not_crash(self) -> None:
        self.spawn(FakeBackend())
        self.prompts(1)
        for n, line in enumerate((b"nope", b"start", b"reload x", b"", b"'"), 2):
            self.type(line + b"\r")
            self.prompts(n)
        self.type(b"quit\r")
        self.finish()


if __name__ == "__main__":
    unittest.main()
