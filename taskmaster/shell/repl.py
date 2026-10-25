from __future__ import annotations

import contextlib
import re
import readline

from taskmaster.protocol import Backend, BackendError
from taskmaster.shell import commands, render

PROMPT = "taskmaster> "


def _names(backend: Backend) -> list[str]:
    """`all`, every program and every instance, as the backend reports them now."""
    names = {"all"}
    for row in backend.status([]).get("data") or []:
        names.add(row["name"])
        names.add(re.sub(r"_\d\d$", "", row["name"]))
    return sorted(names)


def candidates(backend: Backend, before: str, text: str) -> list[str]:
    """What Tab offers for `text`, the word being typed, after the words in `before`."""
    words = before.split()
    if not words:
        pool = list(commands.COMMANDS)
    else:
        cmd = commands.COMMANDS.get(words[0])
        if cmd is None or not cmd.takes_names:
            return []
        pool = _names(backend)
    return sorted(word for word in pool if word.startswith(text))


def _complete_with(backend: Backend) -> None:
    matches: list[str] = []

    def complete(text: str, state: int) -> str | None:
        nonlocal matches
        if state == 0:
            before = readline.get_line_buffer()[: readline.get_begidx()]
            try:
                matches = candidates(backend, before, text)
            except BackendError:
                matches = []
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    # the default delimiters include "-", which is valid in a program name
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")


def run(backend: Backend, *, shutdown_on_quit: bool) -> None:
    """Read lines until quit, Ctrl-D, a lost terminal or a lost backend. The only place the shell prints."""
    _complete_with(backend)
    try:
        while True:
            try:
                line = input(PROMPT)
            except KeyboardInterrupt:
                print()
                continue
            except UnicodeDecodeError:
                print("***Invalid input: not UTF-8")
                continue
            except EOFError:
                print()
                break
            result = commands.execute(line, backend)
            if result.text:
                print(result.text)
            if result.quit:
                break
        if shutdown_on_quit:
            print(render.results(backend.shutdown()))
    except BackendError as e:
        print(e)
    except OSError:
        if shutdown_on_quit:
            with contextlib.suppress(BackendError):
                backend.shutdown()
