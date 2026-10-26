from __future__ import annotations

import logging
import os
from logging.handlers import SysLogHandler


def setup(path: str) -> logging.Logger:
    """The `taskmaster` logger, writing to path. A second call starts over: old handlers go."""
    log = logging.getLogger("taskmaster")
    log.setLevel(logging.INFO)
    log.propagate = False
    for old in log.handlers[:]:
        log.removeHandler(old)
        old.close()
    handler = logging.FileHandler(path, mode="a")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


def add_syslog(log: logging.Logger) -> None:
    """Send WARNING and above to syslog as well, when /dev/log exists."""
    if not os.path.exists("/dev/log"):
        log.warning("syslog: /dev/log is missing, alerts only go to this file")
        return
    handler = SysLogHandler(address="/dev/log")
    handler.ident = "taskmaster: "
    handler.setLevel(logging.WARNING)
    log.addHandler(handler)
