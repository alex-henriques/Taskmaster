from __future__ import annotations

import os
import pty
import re
import select
import signal
import sys
import tempfile
import termios
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = 'programs:\n  sleeper:\n    cmd: "sleep 30"\n    starttime: 0.2\n'
PROMPT = "taskmaster> "


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class MainTest(unittest.TestCase):
    """taskmaster.main on a pty, as a user would run it: Ctrl-C and quit."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "c.yaml"), "w") as f:
            f.write(CONFIG)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            pid, fd = pty.fork()
        except OSError as e:
            self.skipTest(f"needs a pty: {e}")
        if pid == 0:
            try:
                os.chdir(tmp.name)
                os.environ["PYTHONPATH"] = ROOT
                os.execv(
                    sys.executable, [sys.executable, "-m", "taskmaster.main", "c.yaml"]
                )
            finally:
                os._exit(1)
        self.pid, self.fd, self.out, self.child = pid, fd, b"", 0
        self.addCleanup(self.reap)

    def reap(self) -> None:
        for pid in (self.pid, self.child):
            if pid and alive(pid):
                os.kill(pid, signal.SIGKILL)
        if self.pid:
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
                except OSError:
                    chunk = b""
                if not chunk:
                    self.fail(f"child exited, output so far: {self.out!r}")
                self.out += chunk
        return self.out.decode(errors="replace")

    def prompts(self, n: int) -> None:
        self.read_until(lambda text: text.count(PROMPT) >= n)

    def type(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def start_and_find_child(self) -> None:
        self.prompts(1)
        time.sleep(0.5)
        self.type(b"status\r")
        text = self.read_until(lambda text: "RUNNING" in text)
        self.child = int(re.search(r"pid (\d+)", text).group(1))

    def wait_exit(self) -> None:
        """Wait for taskmaster to exit, then check the child is gone and the tty is back."""
        end = time.monotonic() + 5
        while True:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                break
            if time.monotonic() > end:
                self.fail(f"taskmaster did not exit, output: {self.out!r}")
            time.sleep(0.02)
        self.pid = 0
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertFalse(alive(self.child))
        flags = termios.tcgetattr(self.fd)[3]
        self.assertTrue(flags & termios.ECHO, "terminal echo is off")
        self.assertTrue(flags & termios.ICANON, "terminal is not in line mode")

    def test_ctrl_c_kills_nothing_and_quit_stops_everything(self) -> None:
        self.start_and_find_child()
        self.type(b"sta\x03")  # Ctrl-C with a half-typed line
        time.sleep(0.3)
        self.assertTrue(alive(self.pid), "Ctrl-C killed taskmaster")
        self.assertTrue(alive(self.child), "Ctrl-C killed the program")
        self.type(b"\x15quit\r")  # Ctrl-U clears the half-typed line
        self.wait_exit()

    def test_sigterm_at_the_prompt_stops_everything_and_restores_the_tty(self) -> None:
        self.start_and_find_child()
        self.prompts(2)
        os.kill(self.pid, signal.SIGTERM)
        self.wait_exit()
