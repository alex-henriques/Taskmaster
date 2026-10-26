from __future__ import annotations

import os
import sys

from taskmaster.api import Api
from taskmaster.core.config import ConfigError
from taskmaster.protocol import SOCKET_PATH
from taskmaster.server import Server


def daemonize() -> None:
    """Double fork: the daemon is no session leader, so it can never get a terminal."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    if devnull > 2:
        os.close(devnull)


def main(argv: list[str]) -> int:
    foreground = "-n" in argv
    args = [a for a in argv if a != "-n"]
    if len(args) != 1:
        print("usage: python3 -m taskmaster.daemon [-n] CONFIG", file=sys.stderr)
        return 2
    try:
        api = Api(os.path.abspath(args[0]), interactive=False)
        server = Server(SOCKET_PATH, api)
    except (ConfigError, OSError, RuntimeError) as e:
        print(f"taskmasterd: {e}", file=sys.stderr)
        return 1
    if not foreground:
        daemonize()
    server.start()
    try:
        return api.run()
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
