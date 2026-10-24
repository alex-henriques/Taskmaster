from __future__ import annotations

import datetime

from taskmaster.protocol import (
    ALREADY_STARTED,
    BAD_CONFIG,
    BAD_NAME,
    BAD_REQUEST,
    BUSY,
    INTERNAL,
    NOT_RUNNING,
    NOT_TTY,
    UNKNOWN_COMMAND,
)

MESSAGES = {
    BAD_REQUEST: "bad request",
    UNKNOWN_COMMAND: "unknown command",
    BAD_NAME: "no such process",
    ALREADY_STARTED: "already started",
    NOT_RUNNING: "not running",
    BUSY: "busy",
    BAD_CONFIG: "invalid config, nothing changed",
    NOT_TTY: "not a tty program",
    INTERNAL: "internal error",
}


def _uptime(seconds: float) -> str:
    """`0:01:02`, or `1 day, 0:01:02`, as supervisorctl shows it."""
    return str(datetime.timedelta(seconds=max(0, int(seconds))))


def _error(code: str | None, msg: str) -> str:
    """The readable text of an error code, followed by the core's detail if it adds any."""
    text = MESSAGES.get(code or "")
    if text is None or msg.startswith(text):
        return msg
    if msg in ("", (code or "").lower().replace("_", " ")):
        return text
    return f"{text}: {msg}"


def _description(row: dict, now: float) -> str:
    """The third column: pid, and uptime if alive, else the error or the last exit."""
    if row["state"] in ("RUNNING", "STARTING") and row["pid"] is not None:
        return f"pid {row['pid']}, uptime {_uptime(now - row['since'])}"
    if row.get("error"):
        return row["error"]
    last = row.get("exit")
    if isinstance(last, int):
        return f"exit {last}"
    if last is None and row["state"] == "STOPPED":
        return "Not started"
    return last or ""


def status_table(rows: list[dict], now: float) -> str:
    """One line per instance, with the column widths of supervisorctl."""
    name_w = max([30, *(len(r["name"]) for r in rows)]) + 3
    lines = [
        f"{r['name']:<{name_w}}{r['state']:<10}{_description(r, now)}" for r in rows
    ]

    return "\n".join(line.rstrip() for line in lines)


def results(reply: dict) -> str:
    """Per instance lines, the reload summary, the msg, or ERROR (<msg>)."""
    data = reply.get("data")
    if isinstance(data, list):
        return "\n".join(
            f"{r['name']}: {r['msg']}"
            if r["ok"]
            else f"{r['name']}: ERROR ({_error(r.get('error'), r['msg'])})"
            for r in data
        )
    if isinstance(data, dict):
        return ", ".join(
            f"{key}: {', '.join(data[key]) or 'none'}"
            for key in ("added", "changed", "removed")
        )
    if not reply["ok"]:
        return f"ERROR ({_error(reply.get('error'), reply['msg'])})"
    return reply.get("msg") or ""
