from __future__ import annotations

import shlex
import time
from dataclasses import dataclass

from taskmaster.protocol import Backend
from taskmaster.shell import render


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    min_args: int
    max_args: int | None
    takes_names: bool


@dataclass(frozen=True)
class Result:
    text: str
    quit: bool = False
    ok: bool = True


# CONTRACTS §1, without attach (T19). help, usages and completion come from here only.
COMMANDS: dict[str, Command] = {
    c.name: c
    for c in (
        Command(
            "status",
            "status [<name>...]",
            "Show the state of the named processes, or of all of them.",
            0,
            None,
            True,
        ),
        Command(
            "start",
            "start <name>... | all",
            "Start the named processes.",
            1,
            None,
            True,
        ),
        Command(
            "stop", "stop <name>... | all", "Stop the named processes.", 1, None, True
        ),
        Command(
            "restart",
            "restart <name>... | all",
            "Stop, then start the named processes.",
            1,
            None,
            True,
        ),
        Command(
            "reload",
            "reload",
            "Reread the config file and apply the changes.",
            0,
            0,
            False,
        ),
        Command("shutdown", "shutdown", "Stop every process and exit.", 0, 0, False),
        Command(
            "help",
            "help [<command>]",
            "List the commands, or show how to use one.",
            0,
            1,
            False,
        ),
        Command("quit", "quit", "Leave the shell.", 0, 0, False),
    )
}


def _help(args: list[str]) -> Result:
    if not args:
        width = max(len(c.usage) for c in COMMANDS.values())
        lines = ["commands (type help <command>):"]
        lines += [f" {c.usage:<{width}} {c.help}" for c in COMMANDS.values()]
        return Result("\n".join(lines))
    cmd = COMMANDS.get(args[0])
    if cmd is None:
        return Result(f"*** No help on {args[0]}", ok=False)
    return Result(f"{cmd.usage}\n   {cmd.help}")


def execute(line: str, backend: Backend) -> Result:
    """Parse one line, run it against the backend, and render the reply."""
    try:
        words = shlex.split(line)
    except ValueError as e:
        return Result(f"*** Syntax error: {e}", ok=False)
    if not words:
        return Result("")
    cmd = COMMANDS.get(words[0])
    if cmd is None:
        return Result(f"*** Unknown syntax: {line.strip()}", ok=False)
    args = words[1:]
    if len(args) < cmd.min_args or (
        cmd.max_args is not None and len(args) > cmd.max_args
    ):
        return Result(f"usage: {cmd.usage}", ok=False)
    if cmd.name == "help":
        return _help(args)
    if cmd.name == "quit":
        return Result("", quit=True)
    method = getattr(backend, cmd.name)
    reply = method(args) if cmd.takes_names else method()
    if cmd.name == "status" and reply["ok"]:
        text = render.status_table(reply["data"], time.time())
    else:
        text = render.results(reply)
    return Result(text, ok=reply["ok"])
