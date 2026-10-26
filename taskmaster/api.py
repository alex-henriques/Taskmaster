from __future__ import annotations

import os
import socket

from taskmaster.core import config, logger
from taskmaster.core.loop import Loop
from taskmaster.core.process import Process
from taskmaster.core.supervisor import Supervisor
from taskmaster.protocol import Channel
from taskmaster.server import Connection


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

    def get_tty(self, name: str) -> tuple[dict, Process | None]:
        return self._call(self.sup.get_tty, name)

    def attach(self, name: str, rows: int, cols: int) -> tuple[dict, Channel | None]:
        return self._call(self._attach, name, rows, cols)

    def _attach(self, name: str, rows: int, cols: int) -> tuple[dict, Channel | None]:
        """On the core's thread: a socketpair stands in for the accepted connection."""
        reply, process = self.sup.get_tty(name)
        if process is None:
            return reply, None
        ours, theirs = socket.socketpair()
        ours.setblocking(False)
        Connection(self, ours).start_attach(process, rows, cols)
        return reply, Channel(theirs, b"")

    def _call(self, method, *args) -> dict:
        """On the core's own thread, call directly: submit(...).result() would wait for itself."""
        if self.loop.in_loop_thread():
            return method(*args)
        return self.loop.submit(method, *args).result()
