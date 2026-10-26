# Contracts between Person A (core) and Person B (interface)

This file is what the two of us agree on. Change it only after both agree, and warn the other person before merging.

- Sections 1 to 4: what the user can do and what comes back.
- Section 5: the signatures that the two sides call on each other.
- Sections 6 and 7: the bonus, meaning the socket protocol and the attach handshake.

The reasons and the rest of the design are in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Commands

| Command | Arguments | Notes |
|---|---|---|
| `status` | `[name…]` | no names means every instance |
| `start` | `name…` or `all` | |
| `stop` | `name…` or `all` | |
| `restart` | `name…` or `all` | |
| `reload` | none | same as `SIGHUP` |
| `shutdown` | none | stop everything and exit |
| `attach` | one instance name | bonus, section 7 |
| `help` | `[command]` | shell only, never sent to the core |
| `quit` | none | shell only. Inside `taskmaster.main` it stops taskmaster (`shutdown`); inside `taskmaster.ctl` it only closes the client |

A name is `all`, a program (all of its instances) or an instance. If any name does not exist, the whole request fails with `BAD_NAME` and nothing happens. Duplicate names are merged.

**State × command** (per instance)

| State | `start` | `stop` | `restart` |
|---|---|---|---|
| STOPPED, EXITED, FATAL | starts | `NOT_RUNNING` | starts |
| STARTING, RUNNING | `ALREADY_STARTED` | stops | stops, then starts once STOPPED |
| BACKOFF | `ALREADY_STARTED` | STOPPED at once | STOPPED at once, then starts |
| STOPPING | `BUSY` | ok (already stopping) | starts once STOPPED |

`start` and `restart` return `BUSY` in two more cases:
- the instance's program has a pending reload ([ARCHITECTURE §7](ARCHITECTURE.md#7-reload));
- a shutdown has started ([ARCHITECTURE §8](ARCHITECTURE.md#8-shutdown)).

Replies are immediate. `start` answers `starting` and does not wait for RUNNING: `status` shows the outcome.

## 2. Process states and names

The states are the same as in supervisor, with the same meaning: `STOPPED`, `STARTING`, `RUNNING`, `BACKOFF`, `STOPPING`, `EXITED`, `FATAL`. These strings are what `status` replies carry. The transitions are in [ARCHITECTURE §6](ARCHITECTURE.md#6-processes-and-state-machine).

With `numprocs: 1` the instance has the program's name. With more, the instances are `name_00`, `name_01`, and so on.

## 3. Replies and error codes

Every `Backend` method (section 5.2) returns a dict like these. In the bonus, the same dicts travel as JSON.

```jsonc
// status: one row per instance, sorted by name
{"ok": true, "data": [{"name": "nginx", "state": "RUNNING", "pid": 4242, "since": now - 62, "exit": null, "error": null}]}
// start / stop / restart: one result per instance; the top-level ok is true only if all are ok
{"ok": false, "data": [{"name": "nginx", "ok": true, "msg": "starting"},
                       {"name": "vogsphere_00", "ok": false, "error": "ALREADY_STARTED", "msg": "already started"}]}
// reload
{"ok": true, "data": {"added": ["web"], "changed": ["nginx"], "removed": []}}
// shutdown
{"ok": true, "msg": "shutting down"}
// the whole request failed
{"ok": false, "error": "BAD_CONFIG", "msg": "programs.nginx.stoptime: must be a number >= 0"}
```

**`status` fields**
- `pid` is `null` when the process is not alive.
- `since` is the time of the last state change (`time.time()`). Uptime is computed from it.
  The example above is 62 seconds before the reply, so the uptime is `0:01:02`, as in the table.
- `exit` is the last exit: a code, a signal name such as `"SIGKILL"`, or `null`.
- `error` is the error message from `Popen`, or `null`.

The shell builds its table from these fields, for example `pid 4242, uptime 0:01:02` as `supervisorctl` shows it.

**Error codes** (closed list)

| Code | When |
|---|---|
| `BAD_REQUEST` | invalid JSON, wrong or missing fields, line too long (bonus server) |
| `UNKNOWN_COMMAND` | unknown `cmd` (bonus server) |
| `BAD_NAME` | a program or instance that does not exist |
| `ALREADY_STARTED` | `start` in STARTING, RUNNING or BACKOFF |
| `NOT_RUNNING` | `stop` in STOPPED, EXITED or FATAL; `attach` to a process that is not alive |
| `BUSY` | `start` in STOPPING, program with a pending reload, or shutdown in progress |
| `BAD_CONFIG` | reload with an invalid config (nothing changes) |
| `NOT_TTY` | `attach` to a program without `tty: true` |
| `INTERNAL` | a bug: an exception inside a command. It is logged, and taskmaster keeps running |

## 4. Config file

The format is the one in the subject's example (VII.1): a top-level `programs:` key, then one block per program. There are no other top-level keys. The log file and the socket have fixed paths: `taskmaster.log` in the folder taskmaster was started from, and `/tmp/taskmaster.sock`.

| Field | Type | Default | Notes |
|---|---|---|---|
| `cmd` | string | required | split with `shlex.split` and run without a shell, so the pid is the program's; for pipes or redirections write `cmd: "sh -c '…'"` |
| `numprocs` | int ≥ 1 | 1 | |
| `umask` | octal | inherited | accept `022` (PyYAML reads it as octal) and `"022"` |
| `workingdir` | path | inherited | |
| `autostart` | bool | `true` | |
| `autorestart` | `always` \| `never` \| `unexpected` | `unexpected` | `true` / `false` mean `always` / `never` |
| `exitcodes` | int or list of ints | `[0]` | |
| `startretries` | int ≥ 0 | 3 | attempts *after* the first one, as in supervisor |
| `starttime` | seconds ≥ 0 (int or float) | 1 | |
| `stopsignal` | signal name | `TERM` | `TERM` or `SIGTERM`; validate with `signal.Signals` |
| `stoptime` | seconds ≥ 0 (int or float) | 10 | |
| `stdout`, `stderr` | path | discarded | opened in append mode; without the field, output goes to `/dev/null` |
| `env` | map of name → string or int | `{}` | merged over taskmaster's own environment; an int is converted with `str()` |
| `user` (bonus, T14) | user name | unchanged | switching needs root; without root only taskmaster's own user is accepted |
| `tty` (bonus, T18) | bool | `false` | run in a pty so it can be attached; stdout and stderr both go to `stdout`; until T18 is merged it is an unknown field |

**Validation.** An invalid file is refused at startup. On reload the whole file is ignored ([ARCHITECTURE §7](ARCHITECTURE.md#7-reload)).
- Use `yaml.safe_load`, never `yaml.load`, which can build arbitrary Python objects.
- An unknown field is an error, so a typo such as `stoptim` cannot silently fall back to the default.
- Messages carry the full path: `programs.nginx.stoptime: must be a number >= 0`.
- **Names**
  - Program names use only `[A-Za-z0-9_-]`, and `all` is not allowed.
  - The generated instance names (section 2) must not collide.
- **Paths:** relative paths (`workingdir`, `stdout`, `stderr`) count from the config file's folder, because `taskmaster.daemon` changes directory to `/`.
- **Other rules**
  - `user` must exist (`pwd.getpwnam`). Bonus, T14.
  - `tty: true` together with `stderr` is an error. Bonus, T18.
  - `env` values must be a string or an int. An int is converted with `str()`, because the subject's own example has `ANSWER: 42`.
  - Booleans, floats and null are errors that ask for quotes: `DEBUG: yes` arrives as a boolean (YAML's "Norway problem") and `V: 3.10` as the float 3.1.

Each program becomes a `ProgramConfig` ([ARCHITECTURE §10](ARCHITECTURE.md#10-core-internals)) with normalized values: defaults applied, tuples instead of lists, a `signal.Signals`, and absolute paths. `==` between two `ProgramConfig`s decides whether a program "changed" on reload.

## 5. Code interfaces

### 5.1 protocol.py

`taskmaster/protocol.py` imports nothing from the package, so everybody can import it without cycles. It also holds the Backend protocol of §5.2; attach arrives with T19. It starts with the error codes and the reply helpers; the bonus adds the wire format (section 6) and the attach types (section 7).

```python
BAD_REQUEST = "BAD_REQUEST"
UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
BAD_NAME = "BAD_NAME"
ALREADY_STARTED = "ALREADY_STARTED"
NOT_RUNNING = "NOT_RUNNING"
BUSY = "BUSY"
BAD_CONFIG = "BAD_CONFIG"
NOT_TTY = "NOT_TTY"
INTERNAL = "INTERNAL"

class BackendError(Exception): ...          # the backend is gone: the core stopped, or the connection was lost

def ok(data=None, msg: str | None = None) -> dict: ...   # {"ok": True}, plus "data" and "msg" when given
def err(code: str, msg: str) -> dict: ...                # {"ok": False, "error": code, "msg": msg}
```

### 5.2 Backend and Api

The shell only ever calls a *backend*: any object with these methods. There are two: `Api` (`taskmaster/api.py`) for the mandatory part, and `RemoteApi` (`taskmaster/client.py`) for the bonus.

```python
class Backend(Protocol):
    def status(self, names: list[str]) -> dict: ...
    def start(self, names: list[str]) -> dict: ...
    def stop(self, names: list[str]) -> dict: ...
    def restart(self, names: list[str]) -> dict: ...
    def reload(self) -> dict: ...
    def shutdown(self) -> dict: ...
    def attach(self, name: str, rows: int, cols: int) -> tuple[dict, Channel | None]: ...   # bonus
```

- Every method returns a reply dict (section 3). A request that fails is a reply with `ok: False`, not an exception.
- `BackendError` is raised only when the backend is gone: the core has stopped, or the connection was lost. The shell prints the message and exits.
- The shell calls its backend from one thread, one request at a time.

`Api` is the in-process backend. It builds the core, runs it, and hands each call over to the core's thread:

```python
class Api:
    loop: Loop                              # the bonus server registers its sockets here
    def __init__(self, config_path: str, *, interactive: bool): ...
    def run(self) -> int: ...
```

- `__init__` loads the config (`ConfigError` and `OSError` propagate), opens the log, and builds the `Loop` and the `Supervisor`. It starts nothing.
- `run()` runs the core loop on the calling thread, which must be the main thread, and returns the exit code. It returns only after a shutdown.
- `interactive=True` means a shell shares the process, so `SIGINT` is ignored. `False` is the daemon, where `SIGINT` shuts down.
- Each request method is thread-safe. On the core's own thread it calls the `Supervisor` directly. On any other thread it calls `Loop.submit(...).result()`.

### 5.3 What Api uses from the core

Person A provides these. `Api` (Person B) is their only caller.

```python
# taskmaster/core/config.py
class ConfigError(Exception): ...
def load(path: str) -> dict[str, ProgramConfig]: ...

# taskmaster/core/logger.py
def setup(path: str) -> logging.Logger: ...

# taskmaster/core/loop.py
class Loop:
    sel: selectors.BaseSelector
    def __init__(self, log: logging.Logger, *, interactive: bool): ...
    def in_loop_thread(self) -> bool: ...
    def submit(self, fn, *args) -> concurrent.futures.Future: ...
    def run(self, sup: Supervisor) -> int: ...

# taskmaster/core/supervisor.py
class Supervisor:
    def __init__(self, config_path: str, configs: dict[str, ProgramConfig],
                 log: logging.Logger, sel: selectors.BaseSelector): ...
    def status(self, names: list[str]) -> dict: ...
    def start(self, names: list[str]) -> dict: ...
    def stop(self, names: list[str]) -> dict: ...
    def restart(self, names: list[str]) -> dict: ...
    def reload(self) -> dict: ...
    def shutdown(self) -> dict: ...
    def get_tty(self, name: str) -> tuple[dict, Process | None]: ...   # bonus
```

- `Loop.submit` is the only way another thread talks to the core. It queues the call, wakes the loop, and returns a `Future`. If the call raises, the loop logs it and the future holds `err(INTERNAL, ...)`. Once the loop has stopped, `submit` raises `BackendError`.
- `in_loop_thread()` is true only on the loop's thread, and only while `run()` is running. So after `run()` returns, every `Api` call raises `BackendError`, from any thread.
- Each `Supervisor` method returns a reply dict (section 3).
- The rest of the core (processes, deadlines, reload) is Person A's business: [ARCHITECTURE §10](ARCHITECTURE.md#10-core-internals).

## 6. Bonus: socket protocol

`taskmaster.daemon` listens on a UNIX socket, and `taskmaster.ctl` connects to it. The shell code is the same: `RemoteApi` has the `Backend` methods and talks over the socket.

**Transport and framing**
- **Socket:** a UNIX stream socket at `/tmp/taskmaster.sock`.
- **Messages:** one JSON object in UTF-8 per line, ended by `\n`. Encoded JSON never contains a literal `\n`.
- **Framing:** one `recv` can hold half a message, or several. Every connection keeps a `LineBuffer`: handle each full line and keep the rest.
- **Trust boundary:** a line over 64 KiB gets `BAD_REQUEST`, and the connection is closed. The shape of every request is validated.
- **Non-blocking writes:** the daemon's sockets never block. What cannot be sent right away waits in an output buffer for `EVENT_WRITE`.
- **Dead clients:** a `BrokenPipeError` or `ConnectionResetError` closes that connection only.
- **One request at a time:** the client sends a request and waits for its reply before sending the next.

**Request**

```json
{"cmd": "start", "args": ["nginx", "vogsphere"]}
```

`cmd` is a string and `args` is a list of strings. `attach` also carries `rows` and `cols`. Anything else is `BAD_REQUEST`, and an unknown `cmd` is `UNKNOWN_COMMAND`.

**Reply:** the dict that the `Backend` method returns (section 3), as one JSON line.

**Additions to `taskmaster/protocol.py`**

```python
SOCKET_PATH = "/tmp/taskmaster.sock"
MAX_LINE = 64 * 1024

class ProtocolError(Exception): ...

def encode(msg: dict) -> bytes: ...                     # compact JSON + b"\n"

class LineBuffer:
    buf: bytes                                  # bytes received but not consumed yet
    def feed(self, data: bytes) -> None: ...    # ProtocolError if buf passes MAX_LINE without b"\n"
    def next_message(self) -> dict | None: ...  # pops ONE line; None if no full line; ProtocolError if not a JSON object
```

`next_message` pops one line at a time on purpose. After an attach reply, what is left in `buf` is raw program output, not JSON (section 7).

**The client and server classes**

```python
# taskmaster/client.py
class RemoteApi:                                # has the Backend methods
    def __init__(self, path: str = SOCKET_PATH): ...   # connect; BackendError("taskmasterd is not running") if nobody listens
    def close(self) -> None: ...                # a lost connection raises BackendError

# taskmaster/server.py
class Connection:                               # one client; implements Sink (section 7)
    def send(self, msg: dict) -> None: ...          # queue one encoded line
    def send_raw(self, data: bytes) -> None: ...    # queue raw bytes; closes the connection past 1 MiB queued
    def close(self) -> None: ...

class Server:
    def __init__(self, path: str, api: Api): ...
        # stale-socket check, bind (mode 0600), listen; RuntimeError("taskmaster is already running")
    def start(self) -> None: ...                # register the listening socket on api.loop.sel
    def close(self) -> None: ...                # flush what it can without blocking, close every connection, unlink the file
```

**Socket lifecycle**
- **Startup:** if the file exists, try to connect to it.
  - It connects: another taskmaster is running, so exit with an error.
  - `ConnectionRefusedError`: the file is a leftover from a crash, so delete it.
  - Any other error, for example a `PermissionError` because root's daemon is running: exit with an error.
- **Creation:** `bind` under `umask 0o177`, so the file is `0600`. Only whoever started the daemon, or root, can control it.
- **Shutdown:** close every connection and delete the file.

## 7. Bonus: attach

Only for programs with `tty: true`. `attach NAME` connects the terminal to the program's console until the user detaches with `Ctrl-b` then `d`.

```python
# taskmaster/protocol.py
class Sink(Protocol):                           # what the core sends console output to; server.Connection implements it
    def send_raw(self, data: bytes) -> None: ...   # never blocks; closes the sink if more than 1 MiB is queued
    def close(self) -> None: ...

class Channel(NamedTuple):                      # what Backend.attach returns on success
    sock: socket.socket                         # raw byte stream to the program's console
    pending: bytes                              # bytes already read after the reply: program output, show it first

# taskmaster/core/process.py, on Process
    def attach(self, sink: Sink, rows: int, cols: int) -> None: ...
    def detach(self, sink: Sink) -> None: ...
    def write_input(self, data: bytes) -> None: ...
```

`Supervisor.get_tty(name)` returns `(ok(), process)`, or `(err(BAD_NAME | NOT_TTY | NOT_RUNNING, ...), None)`.

**Handshake**
1. The shell calls `backend.attach(name, rows, cols)`, with the terminal size from `os.get_terminal_size()`.
2. `RemoteApi` opens a **new** connection and sends `{"cmd": "attach", "args": ["repl"], "rows": 40, "cols": 120}`. `Api` skips the socket: it makes a `socket.socketpair()` and wraps one end in a `server.Connection`, which the core treats like any accepted connection.
3. The core calls `get_tty(name)`. On an error it sends the error reply, and `attach` returns `(reply, None)`.
4. On ok, the core sends `{"ok": true}` and only then calls `process.attach(connection, rows, cols)`, which sets the window size and sends the last 4 KiB of output.
5. From then on the connection carries raw bytes both ways: bytes in go to `process.write_input`, bytes out come from the console. Closing the connection calls `process.detach`.
6. `attach` returns `(reply, Channel(sock, pending))`. `pending` is whatever followed the reply line in the same `recv`.
