from __future__ import annotations

import contextlib
import logging
import os
import queue
import selectors
import signal
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future
from typing import TYPE_CHECKING

from taskmaster.protocol import INTERNAL, BackendError, err

if TYPE_CHECKING:
    from taskmaster.core.supervisor import Supervisor

SIGNALS = (signal.SIGCHLD, signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGQUIT)


class Loop:
    """The core's main-thread loop: signals, submitted calls and deadlines."""

    def __init__(self, log: logging.Logger, *, interactive: bool) -> None:
        self.log = log
        self.interactive = interactive
        self.sel = selectors.DefaultSelector()
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)
        os.set_blocking(self._w, False)
        self._calls: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread: threading.Thread | None = None
        self._sup: Supervisor | None = None

    def in_loop_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        with self._lock:
            if self._closed:
                raise BackendError("taskmaster is not running")
            self._calls.put((future, fn, args))
            with contextlib.suppress(BlockingIOError):
                os.write(self._w, b"\0")
        return future

    def run(self, sup: Supervisor) -> int:
        old_fd = signal.set_wakeup_fd(self._w, warn_on_full_buffer=False)
        old = {sig: signal.signal(sig, lambda *_: None) for sig in SIGNALS}
        self._thread = threading.current_thread()
        self._sup = sup
        self.sel.register(self._r, selectors.EVENT_READ, self._on_wakeup)
        self.log.info("taskmaster started (pid %d)", os.getpid())
        try:
            self._safe(sup.autostart)
            while not sup.finished:
                for key, mask in self.sel.select(sup.time_to_next_deadline()):
                    self._safe(key.data, key.fileobj, mask)
                self._safe(sup.tick, time.monotonic())
        finally:
            with self._lock:
                self._closed = True
                self._thread = None
            for future, _, _ in self._drain():
                future.set_exception(BackendError("taskmaster is not running"))
            signal.set_wakeup_fd(old_fd)
            for sig, handler in old.items():
                signal.signal(sig, handler)
            self.sel.unregister(self._r)
            os.close(self._r)
            os.close(self._w)
            self.log.info("taskmaster stopped")
        return 0

    def _on_wakeup(self, fd: int, _mask: int) -> None:
        data = b""
        with contextlib.suppress(BlockingIOError):
            while chunk := os.read(fd, 4096):
                data += chunk
        for future, fn, args in self._drain():
            try:
                reply = fn(*args)
            except Exception as e:
                self.log.exception("error in a submitted call")
                reply = err(INTERNAL, str(e))
            future.set_result(reply)
        sup = self._sup
        for signum in dict.fromkeys(data):
            if signum == signal.SIGCHLD:
                self._safe(sup.reap)
            elif signum == signal.SIGHUP:
                self.log.info("received SIGHUP")
                self._safe(sup.reload)
            elif signum in (signal.SIGTERM, signal.SIGQUIT) or (
                signum == signal.SIGINT and not self.interactive
            ):
                self.log.info("received %s", signal.Signals(signum).name)
                self._safe(sup.shutdown)

    def _drain(self) -> Iterator[tuple]:
        """The queued calls, taken one at a time until the queue is empty."""
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                return
            yield call

    def _safe(self, fn, *args) -> None:
        """Run fn; an exception is logged with its traceback, and the loop goes on."""
        try:
            fn(*args)
        except Exception:
            self.log.exception("error in %s", getattr(fn, "__qualname__", fn))
