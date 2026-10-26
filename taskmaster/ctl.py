from __future__ import annotations

import shlex
import sys

from taskmaster.client import RemoteApi
from taskmaster.protocol import BackendError
from taskmaster.shell import commands, repl


def main(argv: list[str]) -> int:
    try:
        remote = RemoteApi()
    except BackendError as e:
        print(e, file=sys.stderr)
        return 1
    try:
        if not argv:
            repl.run(remote, shutdown_on_quit=False)
            return 0
        result = commands.execute(shlex.join(argv), remote)
        print(result.text)
        return 0 if result.ok else 1
    except BackendError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        remote.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
