from __future__ import annotations

import json
import unittest

from taskmaster import protocol
from taskmaster.protocol import MAX_LINE, LineBuffer, ProtocolError, encode
from tests.fakes import FakeBackend


class ReplyTest(unittest.TestCase):
    def test_ok_err_shapes(self) -> None:
        self.assertEqual(protocol.ok(), {"ok": True})
        self.assertEqual(protocol.ok(msg="m"), {"ok": True, "msg": "m"})
        self.assertEqual(protocol.ok([]), {"ok": True, "data": []})
        self.assertEqual(
            protocol.err(protocol.BUSY, "m"), {"ok": False, "error": "BUSY", "msg": "m"}
        )

    def test_codes_equal_their_names(self) -> None:
        for name in (
            "BAD_REQUEST",
            "UNKNOWN_COMMAND",
            "BAD_NAME",
            "ALREADY_STARTED",
            "NOT_RUNNING",
            "BUSY",
            "BAD_CONFIG",
            "NOT_TTY",
            "INTERNAL",
        ):
            self.assertEqual(getattr(protocol, name), name)

    def test_fake_backend(self) -> None:
        backend = FakeBackend()
        self.assertEqual(backend.start(["nope"])["error"], protocol.BAD_NAME)

        reply = backend.stop(["all"])
        self.assertNotEqual(reply.get("error"), protocol.BAD_NAME)
        self.assertEqual(
            [r["name"] for r in reply["data"]], ["broken", "nginx", "vogsphere_00"]
        )

        self.assertEqual(backend.calls, [("start", ["nope"]), ("stop", ["all"])])


class LineBufferTest(unittest.TestCase):
    def test_half_line(self) -> None:
        lines = LineBuffer()
        lines.feed(b'{"cmd": "sta')
        self.assertIsNone(lines.next_message())
        lines.feed(b'tus"}\n')
        self.assertEqual(lines.next_message(), {"cmd": "status"})

    def test_two_lines_one_feed(self) -> None:
        lines = LineBuffer()
        lines.feed(b'{"n": 1}\n{"n": 2}\n')
        self.assertEqual(lines.next_message(), {"n": 1})
        self.assertEqual(lines.next_message(), {"n": 2})
        self.assertIsNone(lines.next_message())

    def test_leftover_is_kept(self) -> None:
        lines = LineBuffer()
        lines.feed(b'{"ok": true}\nRAW')
        self.assertEqual(lines.next_message(), {"ok": True})
        self.assertEqual(lines.buf, b"RAW")

    def test_bad_json(self) -> None:
        for line in (b"nope\n", b"[1, 2]\n", b"\xff\n", b"\n"):
            lines = LineBuffer()
            lines.feed(line)
            with self.assertRaises(ProtocolError):
                lines.next_message()

    def test_line_too_long(self) -> None:
        with self.assertRaises(ProtocolError):
            LineBuffer().feed(b"x" * (MAX_LINE + 1))

    def test_long_tail_after_a_full_line(self) -> None:
        with self.assertRaises(ProtocolError):
            LineBuffer().feed(b'{"a": 1}\n' + b"x" * (MAX_LINE + 1))

    def test_long_full_line(self) -> None:
        lines = LineBuffer()
        lines.feed(b"x" * (MAX_LINE + 1) + b"\n")
        with self.assertRaises(ProtocolError):
            lines.next_message()


class EncodeTest(unittest.TestCase):
    def test_encode(self) -> None:
        data = encode({"a": "x\ny"})
        self.assertEqual(data.count(b"\n"), 1)
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(json.loads(data), {"a": "x\ny"})
