from __future__ import annotations

import contextlib
import enum
import fcntl
import logging
import os
import pwd
import selectors
import signal
import struct
import subprocess
import sys
import termios
import time

from taskmaster.core.config import ProgramConfig
from taskmaster.protocol import ALREADY_STARTED, BUSY, NOT_RUNNING, Sink, err, ok

APPEND = os.O_WRONLY | os.O_CREAT | os.O_APPEND

CTTY = (
    "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
    "os.execvp(sys.argv[1], sys.argv[1:])"
)


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
        self.master: int | None = None
        self.out: int | None = None
        self.sinks: set[Sink] = set()
        self.replay = b""

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
        if self.master is not None:
            self._on_output(self.master, selectors.EVENT_READ)
        self._close_tty()
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
            creds = self._credentials()
            with contextlib.ExitStack() as files:
                argv = cfg.cmd
                stdin = stdout = stderr = subprocess.DEVNULL
                if cfg.tty:
                    argv = (sys.executable, "-I", "-c", CTTY, *cfg.cmd)
                    stdin = stdout = stderr = self._open_tty(files)
                elif cfg.stdout:
                    stdout = files.enter_context(open(cfg.stdout, "ab"))
                if cfg.stderr:
                    stderr = files.enter_context(open(cfg.stderr, "ab"))
                self.popen = subprocess.Popen(
                    argv,
                    cwd=cfg.workingdir,
                    env={**os.environ, **dict(cfg.env)},
                    umask=-1 if cfg.umask is None else cfg.umask,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    **creds,
                )
        except (OSError, ValueError, KeyError) as e:
            self._close_tty()
            self.error = str(e)
            self._died_early(f"spawn failed: {e}")

    def attach(self, sink: Sink, rows: int, cols: int) -> None:
        self._resize(rows, cols)
        sink.send_raw(self.replay)
        self.sinks.add(sink)

    def detach(self, sink: Sink) -> None:
        self.sinks.discard(sink)

    def write_input(self, data: bytes) -> None:
        """Type into the console; whatever does not fit in the pty is dropped."""
        if self.master is None:
            return
        with contextlib.suppress(BlockingIOError):
            os.write(self.master, data)

    def _open_tty(self, files: contextlib.ExitStack) -> int:
        """Open the pty and the stdout file; return the slave, closed by files."""
        self.master, slave = os.openpty()
        self._resize(24, 80)
        files.callback(os.close, slave)
        os.set_blocking(self.master, False)
        self.sel.register(self.master, selectors.EVENT_READ, self._on_output)
        if self.config.stdout:
            self.out = os.open(self.config.stdout, APPEND, 0o666)
        return slave

    def _on_output(self, fd: int, _mask: int) -> None:
        """One read per event, never until empty: a flood must not starve the loop."""
        if fd != self.master:
            return
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._close_tty()
            return
        if self.out is not None:
            os.write(self.out, data)
        self.replay = (self.replay + data)[-4096:]
        for sink in self.sinks.copy():
            sink.send_raw(data)

    def _close_tty(self) -> None:
        """Close the pty, the stdout file and every sink; safe to call twice."""
        if self.master is not None:
            self.sel.unregister(self.master)
            os.close(self.master)
            self.master = None
        if self.out is not None:
            os.close(self.out)
            self.out = None
        for sink in self.sinks.copy():
            sink.close()
        self.sinks.clear()

    def _resize(self, rows: int, cols: int) -> None:
        size = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, size)

    def _credentials(self) -> dict:
        """Popen's user, group and extra_groups: setgroups needs root, even to stay yourself."""
        name = self.config.user
        if name is None:
            return {}
        pw = pwd.getpwnam(name)
        if os.geteuid() == 0:
            groups = os.getgrouplist(name, pw.pw_gid)
            return {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": groups}
        if pw.pw_uid == os.geteuid():
            return {}
        raise PermissionError("user switching needs root")

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
