from __future__ import annotations

import logging
import selectors
import time
from collections.abc import Callable

from taskmaster.core.config import ConfigError, ProgramConfig, load
from taskmaster.core.process import Process
from taskmaster.core.reload import diff
from taskmaster.protocol import BAD_CONFIG, BAD_NAME, BUSY, err, ok


class Supervisor:
    """Owns every Process, resolves names and runs the commands."""

    def __init__(
        self,
        config_path: str,
        configs: dict[str, ProgramConfig],
        log: logging.Logger,
        sel: selectors.BaseSelector,
    ) -> None:
        self.config_path = config_path
        self.target = configs
        self.pending: dict[str, ProgramConfig | None] = {}
        self.log = log
        self.sel = sel
        self.procs: dict[str, Process] = {}
        for cfg in configs.values():
            self._add(cfg)
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
        return self._act(names, lambda p: self._reloading(p) or p.start())

    def stop(self, names: list[str]) -> dict:
        return self._act(names, Process.stop)

    def restart(self, names: list[str]) -> dict:
        if busy := self._busy():
            return busy
        return self._act(names, lambda p: self._reloading(p) or p.restart())

    def reload(self) -> dict:
        if busy := self._busy():
            return busy
        try:
            new = load(self.config_path)
        except ConfigError as e:
            self.log.error("reload failed, nothing changed: %s", e)
            return err(BAD_CONFIG, str(e))
        plan = diff(self.target, new)
        self.target = new
        summary = {
            "added": list(plan.added),
            "changed": list(plan.changed),
            "removed": list(plan.removed),
        }
        self.log.info("reload: added %s, changed %s, removed %s", *summary.values())
        for name in plan.added + plan.changed + plan.removed:
            self.pending[name] = new.get(name)
            for p in self.procs.values():
                if p.config.name == name:
                    p.stop()
        self._apply_pending()
        return ok(summary)

    def shutdown(self) -> dict:
        self.log.info("shutdown request")
        self.shutting_down = True
        self.pending.clear()
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
        self._apply_pending()

    def time_to_next_deadline(self) -> float | None:
        nearest = min(
            (p.deadline for p in self.procs.values() if p.deadline is not None),
            default=None,
        )
        if nearest is None:
            return None
        return max(0.0, nearest - time.monotonic())

    def _add(self, cfg: ProgramConfig) -> list[Process]:
        """Create the instances of one program, STOPPED."""
        new = [Process(n, cfg, self.log, self.sel) for n in cfg.instance_names()]
        self.procs.update((p.name, p) for p in new)
        return new

    def _apply_pending(self) -> None:
        """Replace or delete each pending program once its old instances are gone."""
        for name, cfg in list(self.pending.items()):  # a copy: the loop deletes entries
            reused = set(cfg.instance_names()) if cfg is not None else set()
            old = [
                p
                for p in self.procs.values()
                if p.config.name == name or p.name in reused
            ]
            if any(p.is_alive() for p in old):
                continue
            del self.pending[name]
            for p in old:
                del self.procs[p.name]
            if cfg is None:
                continue
            for p in self._add(cfg):
                if cfg.autostart:
                    p.start()

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

    def _reloading(self, p: Process) -> dict | None:
        """BUSY for an instance whose program waits on a pending reload."""
        if p.config.name in self.pending:
            return {"name": p.name, **err(BUSY, "reload pending")}
        return None
