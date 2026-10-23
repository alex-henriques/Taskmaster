from __future__ import annotations

import datetime


def _uptime(seconds: float) -> str:
    """`0:01:02, or `1 day, 0:01:02`, as supervisorctl shows it."""
    return str(datetime.timedelta(seconds=max(0, int(seconds))))


def _description(row: dict, now: float) -> str:
    """The third column: pid, and uptime if alive, else the error or the last exit."""
    if row["state"] in ("RUNNING", "STARTING") and row["pid"] is not None:
        return f"pid {row['pid']}, uptime {_uptime(now - row['since'])}"
    if row.get("error"):
        return row["error"]
    last = row.get("exit")
    if isinstance(last, int):
        return f"exit {last}"
    return last or ""


def status_table(rows: list[dict], now: float) -> str:
    """One line per instance, with aligned name and state columns"""
    name_w = max((len(r["name"]) for r in rows), default=0)
    state_w = max((len(r["state"]) for r in rows), default=0)
    lines = [
        f"{r['name']:<{name_w}}  {r['state']:<{state_w}}  {_description(r, now)}"
        for r in rows
    ]
    return "\n".join(line.rstrip() for line in lines)


def results(reply: dict) -> str:
    """Per instance lines, the reload summary, the msg, or ERROR (<msg>)."""
    data = reply.get("data")
    if isinstance(data, list):
        return "\n".join(
            f"{r['name']}: {r['msg']}"
            if r["ok"]
            else f"{r['name']}: ERROR ({r['msg']})"
            for r in data
        )
    if isinstance(data, dict):
        return ", ".join(
            f"{key}: {', '.join(data[key]) or 'none'}"
            for key in ("added", "changed", "removed")
        )
    if not reply["ok"]:
        return f"ERROR ({reply['msg']})"
    return reply.get("msg") or ""
