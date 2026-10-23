from __future__ import annotations

import datetime


def _uptime(seconds: float) -> str:
    """`0:01:02, or `1 day, 0:01:02`, as supervisorctl shows it."""
    return str(datetime.timedelta(seconds=max(0, int(seconds))))


def _description(row: dict, now: float) -> str:
    """The third column: pid, and uptime if alive, else the last exit or the error"""
    if row["state"] in ("RUNNING", "STARTING") and row["pid"] is not None:
        return f"pid {row['pid']}, uptime {_uptime(now - row['since'])}"
    last = row.get("exit")
    if isinstance(last, int):
        return f"exit {last}"
    if last is not None:
        return last  # a signal name
    return row.get("error") or ""


def status_table(rows: list[dict], now: float) -> str:
    """One line per instance, with aligned name and state columns"""
    if not rows:
        return ""
    name_w = max(len(r["name"]) for r in rows)
    state_w = max(len(r["state"]) for r in rows)
    lines = [
        f"{r['name']:<{name_w}}  {r['state']:<{state_w}}  {_description(r, now)}"
        for r in rows
    ]
    return "\n".join(line.rstrip() for line in lines)


def _names(names: list[str]) -> str:
    return ", ".join(names) or "none"


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
        return (
            f"added: {_names(data['added'])}, changed: {_names(data['changed'])}, "
            f"removed: {_names(data['removed'])}"
        )
    if not reply["ok"]:
        return f"ERROR ({reply['msg']})"
    return reply.get("msg") or ""
