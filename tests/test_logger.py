from __future__ import annotations

import logging
import os
import tempfile
import unittest
from logging.handlers import SysLogHandler
from unittest import mock

from taskmaster.core import logger


class LoggerTest(unittest.TestCase):
    def tidy(self, log: logging.Logger) -> None:
        """Leave the shared taskmaster logger with no handlers after the test."""
        for handler in log.handlers:
            self.addCleanup(handler.close)
            self.addCleanup(log.removeHandler, handler)

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
            self.assertFalse(log.propagate)
            log.info("hello")
            with open(first) as f:
                self.assertEqual(f.read(), "")
            with open(second) as f:
                line = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO hello\n"
                self.assertRegex(f.read(), f"^{line}$")

    @unittest.skipUnless(os.path.exists("/dev/log"), "needs /dev/log")
    def test_add_syslog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = logger.setup(os.path.join(tmp, "t.log"))
            logger.add_syslog(log)
            self.tidy(log)
            syslog = [h for h in log.handlers if isinstance(h, SysLogHandler)]
            self.assertEqual(len(syslog), 1)
            self.assertEqual(syslog[0].level, logging.WARNING)
            self.assertEqual(syslog[0].ident, "taskmaster: ")

    def test_no_dev_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.log")
            log = logger.setup(path)
            # a test can't remove /dev/log, so pretend it is missing
            with mock.patch("os.path.exists", return_value=False):
                logger.add_syslog(log)
            self.tidy(log)
            self.assertEqual(len(log.handlers), 1)
            with open(path) as f:
                lines = f.read().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn(" WARNING ", lines[0])

    def test_setup_drops_old_handlers(self) -> None:
        logging.getLogger("taskmaster").addHandler(logging.NullHandler())
        with tempfile.TemporaryDirectory() as tmp:
            log = logger.setup(os.path.join(tmp, "t.log"))
            self.tidy(log)
            self.assertEqual(len(log.handlers), 1)


if __name__ == "__main__":
    unittest.main()
