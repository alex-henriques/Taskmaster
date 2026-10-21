from __future__ import annotations
import unittest
from taskmaster import protocol
from tests.fakes import FakeBackend

class ReplyTest(unittest.TestCase):
    def test_ok_without_data(self) -> None:
        self.assertEqual(protocol.ok(), {"ok": True})

    def test_ok_keeps_empty_data(self) -> None:
        self.assertEqual(protocol.ok([]), {"ok": True, "data": []})

    def test_ok_err_shapes(self) -> None:
        self.assertEqual(protocol.ok(), {"ok": True})
        self.assertEqual(protocol.ok(msg="m"), {"ok": True, "msg": "m"})
        self.assertEqual(protocol.ok([]), {"ok": True, "data": []})
        self.assertEqual(protocol.err(protocol.BUSY, "m"), {"ok": False, "error": "BUSY", "msg": "m"})

    def test_codes_equal_their_names(self) -> None:
        codes = {k: v for k, v in vars(protocol).items()
                 if k.isupper() and isinstance(v, str)}
        self.assertEqual(len(codes), 9)
        for name, value in codes.items():
            self.assertEqual(name, value)

    def test_fake_backend(self) -> None:
        backend = FakeBackend()
        self.assertEqual(backend.start(["nope"])["error"], protocol.BAD_NAME)
        self.assertTrue(backend.stop(["all"])["ok"])
        self.assertEqual(backend.calls, [("start", ["nope"]), ("stop", ["all"])])

