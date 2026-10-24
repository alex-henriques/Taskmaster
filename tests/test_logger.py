from __future__ import annotations

import logging
import os
import tempfile
import unittest

from taskmaster.core import logger


class LoggerTest(unittest.TestCase):
    def test_setup_twice_keeps_one_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "first.log")
            second = os.path.join(tmp, "second.log")
            logger.setup(first)
            log = logger.setup(second)
            self.assertEqual(len(log.handlers), 1)
            handler = log.handlers[0]
            self.addCleanup(log.removeHandler, handler)
            self.addCleanup(handler.close)
            self.assertEqual(log.level, logging.INFO)
            log.info("hello")
            with open(first) as f:
                self.assertEqual(f.read(), "")
            with open(second) as f:
                line = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO hello\n"
                self.assertRegex(f.read(), f"^{line}$")


if __name__ == "__main__":
    unittest.main()
