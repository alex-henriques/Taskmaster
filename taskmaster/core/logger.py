from __future__ import annotations

import logging


def setup(path: str) -> logging.Logger:
    """The `taskmaster` logger, writing to path. A second call swaps the file, never adds one."""
    log = logging.getLogger("taskmaster")
    log.setLevel(logging.INFO)
    log.propagate = False
    for old in [h for h in log.handlers if isinstance(h, logging.FileHandler)]:
        log.removeHandler(old)
        old.close()
    handler = logging.FileHandler(path, mode="a")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log
