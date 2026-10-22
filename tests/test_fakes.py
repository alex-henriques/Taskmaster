from __future__ import annotations
import unittest
from tests.fakes import FakeBackend


class FakeBackendTest(unittest.TestCase):
    def test_start_running_is_already_started(self) -> None:
        reply = FakeBackend().start(["nginx"])
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["data"][0]["error"], "ALREADY_STARTED")

    def test_start_stopped_is_ok(self) -> None:
        self.assertTrue(FakeBackend().start(["vogsphere_00"])["ok"])

    def test_shutdown(self) -> None:
        self.assertEqual(FakeBackend().shutdown(), {"ok": True, "msg": "shutting down"})
