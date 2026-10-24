from __future__ import annotations

import logging
import selectors
import time
from collections.abc import Callable

from taskmaster.core.config import ProgramConfig
from taskmaster.core.process import Process
from taskmaster.protocol import BAD_NAME, BUSY, INTERNAL, err, ok


class Supervisor:
    """Owns every Process, resolves names and runs the commands."""

    def __init__(
        self,
        config_path: str,
        configs: dict[str, ProgramConfig],
        log: logging.Logger,
        sel: selectors.BaseSelector,
    ) -> None:
        self.procs = {
            name: Process(name, cfg, log, sel)
            for cfg in configs.values()
            for name in cfg.instance_names()
        }
        self.log = log
        self.shutting_down = False

    @property
    def finished(self) -> bool:
        """True once a shutdown has started and no child is left alive."""
        return self.shutting_down and not any(p.is_alive() for p in self.procs.values())

    def autostart(self) -> None:
        for p in self.procs.values():
            if p.config.autostart:
                p.start()

    def status(self, names: list[str]) -> dict:
        if bad := self._bad_name(names):
            return bad
        self.reap()
        return ok([p.info() for p in self._select(names or ["all"])])

    def start(self, names: list[str]) -> dict:
        if busy := self._busy():
            return busy
        return self._act(names, Process.start)

    def stop(self, names: list[str]) -> dict:
        return self._act(names, Process.stop)

    def restart(self, names: list[str]) -> dict:
        if busy := self._busy():
            return busy
        return self._act(names, Process.restart)

    def reload(self) -> dict:
        if busy := self._busy():
            return busy
        return err(INTERNAL, "reload arrives with issue #12")

    def shutdown(self) -> dict:
        self.log.info("shutdown request")
        self.shutting_down = True
        for p in self.procs.values():
            p.stop()
        return ok(msg="shutting down")

    def reap(self) -> None:
        for p in self.procs.values():
            p.poll()

    def tick(self, now: float) -> None:
        for p in self.procs.values():
            if p.deadline is not None and p.deadline <= now:
                p.on_deadline(now)

    def time_to_next_deadline(self) -> float | None:
        nearest = min(
            (p.deadline for p in self.procs.values() if p.deadline is not None),
            default=None,
        )
        if nearest is None:
            return None
        return max(0.0, nearest - time.monotonic())

    def _select(self, names: list[str]) -> list[Process]:
        return [
            p
            for _, p in sorted(self.procs.items())
            if any(n in ("all", p.name, p.config.name) for n in names)
        ]

    def _bad_name(self, names: list[str]) -> dict | None:
        for n in names:
            if n != "all" and not self._select([n]):
                return err(BAD_NAME, f"no such process: {n}")
        return None

    def _act(self, names: list[str], command: Callable[[Process], dict]) -> dict:
        if bad := self._bad_name(names):
            return bad
        results = [command(p) for p in self._select(names)]
        return {"ok": all(r["ok"] for r in results), "data": results}

    def _busy(self) -> dict | None:
        """BUSY once a shutdown has started, for the commands that would start something"""
        return err(BUSY, "shutting down") if self.shutting_down else None
