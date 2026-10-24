from __future__ import annotations

import sys
import termios
import threading

from taskmaster.api import Api
from taskmaster.core.config import ConfigError
from taskmaster.shell import repl


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 -m taskmaster.main CONFIG", file=sys.stderr)
        return 2
    try:
        api = Api(argv[0], interactive=True)
    except (ConfigError, OSError) as e:
        print(f"taskmaster: {e}", file=sys.stderr)
        return 1
    saved = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
    threading.Thread(
        target=repl.run, args=(api,), kwargs={"shutdown_on_quit": True}, daemon=True
    ).start()
    try:
        return api.run()
    finally:
        if saved is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
