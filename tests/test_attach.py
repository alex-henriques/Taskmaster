from __future__ import annotations

import os
import pty
import select
import socket
import termios
import threading
import time
import unittest

from taskmaster.protocol import Channel
from taskmaster.shell import attach


class KeysTest(unittest.TestCase):
    def test_plain_keys_pass(self) -> None:
        self.assertEqual(attach.Keys().filter(b"ls\n"), (b"ls\n", False))

    def test_ctrl_b_d_detaches(self) -> None:
        self.assertEqual(attach.Keys().filter(b"ab\x02dcd"), (b"ab", True))

    def test_ctrl_b_then_another_key_sends_both(self) -> None:
        self.assertEqual(attach.Keys().filter(b"\x02x"), (b"\x02x", False))
        self.assertEqual(attach.Keys().filter(b"\x02\x02"), (b"\x02\x02", False))

    def test_prefix_survives_two_reads(self) -> None:
        keys = attach.Keys()
        self.assertEqual(keys.filter(b"a\x02"), (b"a", False))
        self.assertEqual(keys.filter(b"d"), (b"", True))

    def test_prefix_then_other_key_across_reads(self) -> None:
        keys = attach.Keys()
        self.assertEqual(keys.filter(b"\x02"), (b"", False))
        self.assertEqual(keys.filter(b"q"), (b"\x02q", False))


class SessionTest(unittest.TestCase):
    """The session on a pty pair that stands in for the user's terminal."""

    def setUp(self) -> None:
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.before = termios.tcgetattr(self.slave)
        self.mine, self.theirs = socket.socketpair()
        self.addCleanup(self.theirs.close)
        self.result: list[str] = []

    def start(self, pending: bytes = b"") -> threading.Thread:
        def body() -> None:
            channel = Channel(self.mine, pending)
            self.result.append(attach.session(channel, self.slave, self.slave))

        thread = threading.Thread(target=body)
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread

    def screen(self, want: bytes, timeout: float = 3.0) -> bytes:
        """What the terminal shows, until `want` is in it."""
        seen = b""
        end = time.monotonic() + timeout
        while want not in seen:
            if time.monotonic() > end:
                self.fail(f"{want!r} never shown, only {seen!r}")
            if select.select([self.master], [], [], 0.05)[0]:
                seen += os.read(self.master, 4096)
        return seen

    def wait_raw(self) -> None:
        end = time.monotonic() + 3
        while termios.tcgetattr(self.slave) == self.before:
            if time.monotonic() > end:
                self.fail("the terminal never went raw")
            time.sleep(0.01)

    def test_pending_then_output_then_input(self) -> None:
        thread = self.start(pending=b"old\r\n")
        self.screen(b"old")
        self.wait_raw()
        self.theirs.sendall(b"new\r\n")
        self.screen(b"new")
        os.write(self.master, b"hi\n")
        self.assertEqual(self.theirs.recv(100), b"hi\n")
        os.write(self.master, b"\x02d")
        thread.join(3)
        self.assertEqual(self.result, [""])
        self.assertEqual(termios.tcgetattr(self.slave), self.before)

    def test_ctrl_c_goes_to_the_program(self) -> None:
        thread = self.start()
        self.wait_raw()
        os.write(self.master, b"\x03")
        self.assertEqual(self.theirs.recv(10), b"\x03")
        os.write(self.master, b"\x02d")
        thread.join(3)

    def test_program_ends(self) -> None:
        thread = self.start()
        self.wait_raw()
        self.theirs.close()
        thread.join(3)
        self.assertEqual(self.result, [attach.LOST])
        self.assertEqual(termios.tcgetattr(self.slave), self.before)

    def test_core_gone_while_writing(self) -> None:
        thread = self.start()
        self.wait_raw()
        self.theirs.close()
        os.write(self.master, b"x")
        thread.join(3)
        self.assertEqual(self.result, [attach.LOST])
        self.assertEqual(termios.tcgetattr(self.slave), self.before)


if __name__ == "__main__":
    unittest.main()
