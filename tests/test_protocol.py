from __future__ import annotations

import unittest

from taskmaster import protocol
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
