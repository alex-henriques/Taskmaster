from __future__ import annotations

import contextlib
import enum
import logging
import os
import selectors
import signal
import subprocess
import time

from taskmaster.core.config import ProgramConfig
from taskmaster.protocol import ALREADY_STARTED, BUSY, NOT_RUNNING, err, ok


class State(enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    BACKOFF = "BACKOFF"
    STOPPING = "STOPPING"
    EXITED = "EXITED"
    FATAL = "FATAL"


class Process:
    """One instance and its child, driven by the transition table."""

    def __init__(
        self,
        name: str,
        config: ProgramConfig,
        log: logging.Logger,
        sel: selectors.BaseSelector,
    ) -> None:
        self.name = name
        self.config = config
        self.log = log
        self.sel = sel  # only the bonus `pty` uses it
        self.state = State.STOPPED
        self.deadline: float | None = None
        self.popen: subprocess.Popen | None = None
        self.attempts = 0
        self.since = time.time()
        self.exit: int | str | None = None
        self.error: str | None = None
        self.restart_pending = False

    def is_alive(self) -> bool:
        return self.popen is not None

    def info(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "pid": self.popen.pid if self.popen else None,
            "since": self.since,
            "exit": self.exit,
            "error": self.error,
        }

    def start(self) -> dict:
        if self.state in (State.STARTING, State.RUNNING, State.BACKOFF):
            return self._refused(ALREADY_STARTED)
        if self.state is State.STOPPING:
            return self._refused(BUSY)
        self.attempts = 0
        self._spawn()
        return self._ok("starting")

    def stop(self) -> dict:
        self.restart_pending = False
        if self.state in (State.STOPPED, State.EXITED, State.FATAL):
            return self._refused(NOT_RUNNING)
        if self.state is State.STOPPING:
            return self._ok("already stopping")
        if self.state is State.BACKOFF:
            self.deadline = None
            self._set(State.STOPPED, "stop during backoff")
            return self._ok("stopped")
        self._killpg(self.config.stopsignal)
        self.deadline = time.monotonic() + self.config.stoptime
        self._set(State.STOPPING, f"stop, sent {self.config.stopsignal.name}")
        return self._ok("stopping")

    def restart(self) -> dict:
        self.log.info("%s: restart", self.name)
        if self.state in (State.STOPPED, State.EXITED, State.FATAL):
            self.start()
        elif self.state is State.BACKOFF:
            self.stop()
            self.start()
        elif self.state is State.STOPPING:
            self.restart_pending = True
        else:
            self.stop()
            self.restart_pending = True
        return self._ok("restarting")

    def poll(self) -> None:
        if self.popen is None or self.popen.poll() is None:
            return
        code = self.popen.returncode
        self.popen = None
        try:
            self.exit = signal.Signals(-code).name if code < 0 else code
        except ValueError:
            self.exit = f"SIG{-code}"
        if self.state is State.STARTING:
            self._died_early(f"exited early with {self.exit}")
        elif self.state is State.RUNNING:
            self._exited(code)
        elif self.state is State.STOPPING:
            self.deadline = None
            self._set(State.STOPPED, f"exit {self.exit}")
            if self.restart_pending:
                self.restart_pending = False
                self.start()

    def on_deadline(self, now: float) -> None:
        self.poll()
        if self.deadline is None or now < self.deadline:
            return
        self.deadline = None
        if self.state is State.STARTING:
            self.attempts = 0
            self._set(State.RUNNING, f"alive after {self.config.starttime} s")
        elif self.state is State.BACKOFF:
            self._spawn()
        elif self.state is State.STOPPING:
            self._killpg(signal.SIGKILL)
            self.log.warning("%s: killed after stoptime", self.name)

    def _spawn(self) -> None:
        cfg = self.config
        self.attempts += 1
        self.deadline = time.monotonic() + cfg.starttime
        self._set(State.STARTING, f"spawn, attempt {self.attempts}")
        self.error = None
        try:
            with contextlib.ExitStack() as files:
                stdout = stderr = subprocess.DEVNULL
                if cfg.stdout:
                    stdout = files.enter_context(open(cfg.stdout, "ab"))
                if cfg.stderr:
                    stderr = files.enter_context(open(cfg.stderr, "ab"))
                self.popen = subprocess.Popen(
                    cfg.cmd,
                    cwd=cfg.workingdir,
                    env={**os.environ, **dict(cfg.env)},
                    umask=-1 if cfg.umask is None else cfg.umask,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
        except (OSError, ValueError) as e:
            self.error = str(e)
            self._died_early(f"spawn failed: {e}")

    def _died_early(self, reason: str) -> None:
        if self.attempts > self.config.startretries:
            self.deadline = None
            self._set(State.FATAL, f"{reason}; gave up", logging.WARNING)
        else:
            self.deadline = time.monotonic() + self.attempts
            self._set(State.BACKOFF, f"{reason}; retry in {self.attempts} s")

    def _exited(self, code: int) -> None:
        expected = code >= 0 and code in self.config.exitcodes
        level = logging.INFO if expected else logging.WARNING
        word = "expected" if expected else "unexpected"
        self._set(State.EXITED, f"exit {self.exit}, {word}", level)
        policy = self.config.autorestart
        if policy == "always" or (policy == "unexpected" and not expected):
            self._spawn()

    def _killpg(self, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.popen.pid, sig)

    def _set(self, state: State, reason: str, level: int = logging.INFO) -> None:
        old = self.state.value
        self.log.log(level, "%s: %s -> %s (%s)", self.name, old, state.value, reason)
        self.state = state
        self.since = time.time()

    def _ok(self, msg: str) -> dict:
        return {"name": self.name, **ok(msg=msg)}

    def _refused(self, code: str) -> dict:
        return {"name": self.name, **err(code, code.lower().replace("_", " "))}
