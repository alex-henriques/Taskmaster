from __future__ import annotations

import re
import time

from taskmaster.protocol import (
    ALREADY_STARTED,
    BAD_NAME,
    BUSY,
    NOT_RUNNING,
    NOT_TTY,
    Channel,
    err,
    ok,
)


def _matches(name: str, row: dict) -> bool:
    """`all`, the instance itself, or its program (`vogsphere` matches `vogsphere_00`)."""
    return (
        name in ("all", row["name"])
        or re.fullmatch(re.escape(name) + r"_\d\d", row["name"]) is not None
    )


# CONTRACTS §1: the error each command gives in each state. Any other state is ok.
REFUSED = {
    "start": {
        "STARTING": ALREADY_STARTED,
        "RUNNING": ALREADY_STARTED,
        "BACKOFF": ALREADY_STARTED,
        "STOPPING": BUSY,
    },
    "stop": {"STOPPED": NOT_RUNNING, "EXITED": NOT_RUNNING, "FATAL": NOT_RUNNING},
    "restart": {},
}


class FakeBackend:
    """A backend with standard replies for testing the shell without the core."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.calls: list[tuple] = []
        if rows is None:
            now = time.time()
            rows = [
                {
                    "name": "nginx",
                    "state": "RUNNING",
                    "pid": 4242,
                    "since": now - 62,
                    "exit": None,
                    "error": None,
                },
                {
                    "name": "vogsphere_00",
                    "state": "STOPPED",
                    "pid": None,
                    "since": now - 30,
                    "exit": None,
                    "error": None,
                },
                {
                    "name": "broken",
                    "state": "FATAL",
                    "pid": None,
                    "since": now - 5,
                    "exit": None,
                    "error": "can't find command 'nope'",
                },
            ]
        self.rows = rows

    def _select(self, names: list[str]) -> list[dict]:
        """The rows the names point to, each once, sorted by name."""
        rows = [r for r in self.rows if any(_matches(n, r) for n in names)]
        return sorted(rows, key=lambda r: r["name"])

    def _begin(self, verb: str, names: list[str]) -> dict | None:
        """Record the call. Return BAD_NAME if a name points to no row."""
        self.calls.append((verb, list(names)))
        for n in names:
            if not self._select([n]):
                return err(BAD_NAME, f"no such process: {n}")
        return None

    def _act(self, verb: str, msg: str, names: list[str]) -> dict:
        """One result per selected row; a row gets an error code if its state refuses the command."""
        if bad := self._begin(verb, names):
            return bad
        results = []
        for row in self._select(names):
            code = REFUSED[verb].get(row["state"])
            if code is None:
                results.append({"name": row["name"], "ok": True, "msg": msg})
            else:
                results.append(
                    {
                        "name": row["name"],
                        "ok": False,
                        "error": code,
                        "msg": code.lower().replace("_", " "),
                    }
                )
        return {"ok": all(r["ok"] for r in results), "data": results}

    def status(self, names: list[str]) -> dict:
        if bad := self._begin("status", names):
            return bad
        [r for r in self.rows if not names or "all" in names or r["name"] in names]
        return ok([dict(r) for r in self._select(names or ["all"])])

    def start(self, names: list[str]) -> dict:
        return self._act("start", "starting", names)

    def stop(self, names: list[str]) -> dict:
        return self._act("stop", "stopped", names)

    def restart(self, names: list[str]) -> dict:
        return self._act("restart", "restarting", names)

    def reload(self) -> dict:
        self.calls.append(("reload", ()))
        return ok({"added": [], "changed": [], "removed": []})

    def shutdown(self) -> dict:
        self.calls.append(("shutdown", ()))
        return ok(msg="shutting down")

    def attach(self, name: str, rows: int, cols: int) -> tuple[dict, Channel | None]:
        self.calls.append(("attach", name, rows, cols))
        if not self._select([name]):
            return err(BAD_NAME, f"no such process: {name}"), None
        return err(NOT_TTY, f"{name} does not run in a tty"), None
