from __future__ import annotations
import time
from taskmaster.protocol import ALREADY_STARTED, BAD_NAME, err, ok

class FakeBackend:
    """A backend with standard replies for testing the shell without the core."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.calls: list[tuple] = []
        if rows is None:
            now = time.time()
            rows = [{"name": "nginx", "state": "RUNNING", "pid": 4242, "since": now - 62, "exit": None, "error": None},
                    {"name": "vogsphere_00", "state": "STOPPED", "pid": None, "since": now - 30, "exit": None, "error": None},
                    {"name": "broken", "state": "FATAL", "pid": None, "since": now - 5, "exit": None, "error": "can't find command 'nope'"},]
        self.rows = rows

    def _known(self, name:str) -> bool:
        return name == "all" or any(r["name"] == name for r in self.rows)

    def _action(self, verb: str, msg: str, names: list[str]) -> dict:
        self.calls.append((verb, names))
        bad = [n for n in names if not self._known(n)]
        if bad:
            return err(BAD_NAME, f"no such process: {bad[0]}")
        results = [{"name": n, "ok": True, "msg": msg} for n in names]
        return ok(results)

    def status(self, names: list[str]) -> dict:
        self.calls.append(("status", names))
        rows = [r for r in self.rows if not names or "all" in names or r["name"] in names]
        return ok(rows)

    def start(self, names: list[str]) -> dict:
        self.calls.append(("start", names))
        bad = [n for n in names if not self._known(n)]
        if bad:
            return err(BAD_NAME, f"no such process: {bad[0]}")
        running = {r["name"] for r in self.rows
                   if r["state"] in ("STARTING", "RUNNING", "BACKOFF")}
        results = []
        for n in names:
            if n in running:
                results.append({"name": n, "ok": False, "error": ALREADY_STARTED, "msg": "already started"})
            else:
                results.append({"name": n, "ok": True, "msg": "starting"})
        return {"ok": all(r["ok"] for r in results), "data": results}

    def stop(self, names: list[str]) -> dict:
        return self._action("stop", "stopped", names)

    def restart(self, names: list[str]) -> dict:
        return self._action("restart", "restarting", names)

    def reload(self) -> dict:
        self.calls.append(("reload", ()))
        return ok({"added": [], "changed": [], "removed": []})

    def shutdown(self) -> dict:
        self.calls.append(("shutdown", ()))
        return ok(msg="shutting down")
