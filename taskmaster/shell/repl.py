from __future__ import annotations

import readline  # noqa: F401  (importing it gives input() line editing and history)

from taskmaster.protocol import Backend, BackendError
from taskmaster.shell import commands, render

PROMPT = "taskmaster> "


def run(backend: Backend, *, shutdown_on_quit: bool) -> None:
    """Read lines until quit, Ctrl-D or a lost backend. The only place the shell prints."""
    try:
        while True:
            try:
                line = input(PROMPT)
            except KeyboardInterrupt:
                print()
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
