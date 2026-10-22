# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Taskmaster: a 42-school process supervisor in the spirit of `supervisord`, in Python, written by two people. The design is fully specified before the code, so **read the docs before writing anything**:

- `docs/ARCHITECTURE.md`: scope, threads, state machine, reload, shutdown, and the internal signatures (§10).
- `docs/CONTRACTS.md`: what core and shell agreed on: commands, the state × command table, reply shapes, error codes, config keys, `Backend`/`Api` signatures.
- If a GitHub issue and these docs disagree, the docs win. GitHub issue `#N` is task `TNN` (T05 = #5).

Most modules are still empty stubs. Only `taskmaster/protocol.py` and `tests/fakes.py` have real code so far.

## Commands

```bash
make install                       # .venv with PyYAML + ruff; also sets core.hooksPath to .githooks
make test                          # .venv/bin/python3 -m unittest (run from the repo root)
make run CONFIG=path/to/config.yaml
make clean                         # removes .venv, taskmaster.log, __pycache__

.venv/bin/python3 -m unittest tests.test_protocol -v                              # one file
.venv/bin/python3 -m unittest tests.test_protocol.ReplyTest.test_ok_err_shapes    # one test

.venv/bin/ruff check . && .venv/bin/ruff format --check .   # what the pre-commit hook runs
.venv/bin/ruff check --fix . && .venv/bin/ruff format .      # fix it when the hook blocks a commit
```

CI (`.github/workflows/ci.yml`) runs only `python3 -m unittest -v` on Python 3.10 and 3.14. It does not run ruff, so the hook is the only lint gate.

## Hard constraints

- **Python ≥ 3.10**, because the school machines have 3.10.12. Nothing from 3.11+. `match` is allowed.
- **Standard library + PyYAML only.** Tests use `unittest`, never `pytest`.
- Every module starts with `from __future__ import annotations`.
- Mandatory issues carry no bonus code: `user`, `tty`, `attach`, the socket, `server.py`/`client.py`/`daemon.py`/`ctl.py` arrive only with T14–T19, and bonus work starts only after #13 is closed.

## Architecture in one screen

One process (`python3 -m taskmaster.main CONFIG`), two threads:

- **Core (`taskmaster/core/`, Person A, viceda-s)** runs in the **main thread** on a single `selectors` loop (`core/loop.py`, not created yet). It is the only owner of process state, so there are no locks. It **never blocks** (no `sleep`, no blocking `wait`/write) and never prints or reads the terminal. Signals reach it through `signal.set_wakeup_fd`, which is why it must be the main thread.
- **Shell (`taskmaster/shell/`, `api.py`, `main.py`, `demo/`, Person B, alex-henriques)** runs in a second thread and only talks to a **backend** (the `Backend` protocol in `protocol.py`): `Api` in-process for the mandatory part, `RemoteApi` over a UNIX socket for the bonus, `tests/fakes.FakeBackend` in tests. `Api` methods are `Loop.submit(fn, *args).result()`; `submit` is the only way another thread touches the core.
- **Replies are plain dicts** built with `protocol.ok()` / `protocol.err(CODE, msg)`, the same dicts that go over the wire as JSON in the bonus. A failed request is a reply with `ok: False`, never an exception. `BackendError` is raised only when the backend is gone. Error codes are a closed list (CONTRACTS §3).
- `protocol.py` imports nothing from the package, so both sides can import it without cycles.
- `FakeBackend` encodes the CONTRACTS §1 state × command table, so the shell can be built and tested without the core.

Design decisions that were tested and must not be "simplified" away (ARCHITECTURE §5–§7):

- Reap by calling `popen.poll()` on **every** live process on `SIGCHLD`. Never `os.waitpid(-1, …)`: it steals the exit status from `Popen`, which then reports `0`.
- Never `SIG_IGN` a signal in taskmaster: it stays ignored in children after `exec`. Install an empty Python handler instead.
- Spawn with `start_new_session=True` and signal with `os.killpg`, so `Ctrl-C` never reaches children and grandchildren are not orphaned. Ignore `ProcessLookupError` from `killpg`.
- At most one deadline per instance (`starttime`, backoff retry, or `SIGKILL` after `stoptime`), on `time.monotonic()`.
- Reload: "unchanged" means equal normalized `ProgramConfig` (frozen dataclass `==`). Unchanged programs are never touched. "Changed" means stop, then re-add, through a per-program `pending` entry applied in `tick()`.
- Config uses `yaml.safe_load`. An unknown field is an error, and error messages carry the full path (`programs.nginx.stoptime: …`).
- Tests may sleep, the core may not. A test that needs root, `/dev/log` or a pty must skip itself so CI stays green.

## Team workflow (CONTRIBUTING.md)

- Never push to `main`. Each person works on a long-lived branch (`morcão` for viceda-s, `morcona` for alex-henriques) and opens a PR into `main` with one `Closes #N` line per finished issue; the other person reviews.
- Merge with `gh pr merge --merge`, never squash, never `--delete-branch`. Update with `git merge origin/main`, never rebase a pushed branch.
- `docs/CONTRACTS.md` changes only after both agree, in the same PR as the code, flagged "contract change" in the PR body.
- Commit messages: `type(scope): what changed (#N)`. Types: `feat fix test docs refactor chore`. Scopes: `core shell docs ci`.
- No AI `Co-Authored-By` trailers in commits, and no "Generated with Claude Code" line in PR bodies.
- Both people must be able to explain every line in the defense, the other person's code included.
