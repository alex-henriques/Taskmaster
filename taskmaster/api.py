from __future__ import annotations

import os

from taskmaster.core import config, logger
from taskmaster.core.loop import Loop
from taskmaster.core.supervisor import Supervisor


class Api:
    """The in process Backend: every call is handed over to the core's thread."""

    def __init__(self, config_path: str, *, interactive: bool) -> None:
        configs = config.load(config_path)
        log = logger.setup(os.path.abspath("taskmaster.log"))
        logger.add_syslog(log)
        self.loop = Loop(log, interactive=interactive)
        self.sup = Supervisor(config_path, configs, log, self.loop.sel)

    def run(self) -> int:
        return self.loop.run(self.sup)

    def status(self, names: list[str]) -> dict:
        return self._call(self.sup.status, names)

    def start(self, names: list[str]) -> dict:
        return self._call(self.sup.start, names)

    def stop(self, names: list[str]) -> dict:
        return self._call(self.sup.stop, names)

    def restart(self, names: list[str]) -> dict:
        return self._call(self.sup.restart, names)

    def reload(self) -> dict:
        return self._call(self.sup.reload)

    def shutdown(self) -> dict:
        return self._call(self.sup.shutdown)

    def _call(self, method, *args) -> dict:
        """On the core's own thread, call directly: submit(...).result() would wait for itself."""
        if self.loop.in_loop_thread():
            return method(*args)
        return self.loop.submit(method, *args).result()
