_This project has been created as part of the 42 curriculum by alehenri and viceda-s._

# Taskmaster

## Description

Taskmaster looks after other programs for you. You list them in a configuration
file, and Taskmaster starts them, keeps an eye on them, and, if you want, starts
them again when they crash. At any moment it knows which ones are running and
which ones have stopped.

You can change the configuration while Taskmaster is running. Send it a `SIGHUP`
signal and it reads the file again and applies the changes, without restarting
the programs whose settings stayed the same. Everything that happens, such as a
program starting, stopping or dying unexpectedly, is written to `taskmaster.log`.

Taskmaster stays in the foreground and gives you a small command line to see how
your programs are doing and to start, stop or restart them. It is inspired by
[supervisor](http://supervisord.org/) and written in Python.

## Instructions

You need Linux, Python 3.10 or newer, and `make`. On Debian or Ubuntu, Python
also needs a package to create virtual environments:
`sudo apt install python3-venv`.

```bash
make install                         # create .venv with PyYAML (and ruff, for the commit hook)
make run CONFIG=path/to/config.yaml  # start Taskmaster with your config file
make test                            # run the tests
make clean                           # delete .venv, caches and taskmaster.log
```

Everything is installed inside the project folder, in `.venv`, so the Python
on your system is left untouched.

## Configuration

The config file is YAML with one top-level key, `programs:`, and one block per
program:

```yaml
programs:
  web:
    cmd: "python3 -m http.server 8000"
    numprocs: 2
    autorestart: unexpected
    exitcodes: [0, 2]
    stdout: /tmp/web.out
```

Only `cmd` is required. Relative paths (`workingdir`, `stdout`, `stderr`) are
relative to the config file's folder. An unknown field or a wrong value is an
error, and the message names the exact path, such as
`programs.web.stoptime: must be a number >= 0`.

| Option | Default | What it does |
| --- | --- | --- |
| `cmd` | required | The command to run. It is split like a shell would split it, but no shell is used, so the pid is the program's own. For pipes or redirections write `cmd: "sh -c '…'"`. |
| `numprocs` | `1` | How many copies to run. With more than one, they are named `name_00`, `name_01`, … |
| `autostart` | `true` | Start the program when Taskmaster starts. |
| `autorestart` | `unexpected` | `always`, `never`, or `unexpected` (only when the exit code is not in `exitcodes`). |
| `exitcodes` | `[0]` | Exit codes that count as a normal exit. A single number or a list. |
| `starttime` | `1` | Seconds the program must stay alive to count as started. |
| `startretries` | `3` | How many times to try again after a failed start before giving up (FATAL). |
| `stopsignal` | `TERM` | Signal sent to stop the program, such as `INT` or `SIGQUIT`. |
| `stoptime` | `10` | Seconds to wait after `stopsignal` before sending `SIGKILL`. |
| `stdout`, `stderr` | discarded | File to append the output to. Without it the output goes to `/dev/null`. |
| `env` | `{}` | Variables added to Taskmaster's own environment. |
| `workingdir` | inherited | Directory to run the program in. |
| `umask` | inherited | Umask of the program, such as `022`. |

## Using it

| Command | What it does |
| --- | --- |
| `status [name…]` | Show the state of every program, or of the ones you name. |
| `start name… \| all` | Start programs. |
| `stop name… \| all` | Stop programs. |
| `restart name… \| all` | Stop, then start again. |
| `reload` | Read the config file again and apply the changes. |
| `shutdown`, `quit` | Stop every program and leave. |
| `help [command]` | List the commands, or show how to use one. |

Signals: `SIGHUP` does the same as `reload`. `SIGTERM` and `SIGQUIT` shut
Taskmaster down like `quit`. `Ctrl-C` is ignored by Taskmaster itself and only
clears the line you are typing. On reload, programs whose settings did not
change are left alone, and a file with an error is refused as a whole, so
nothing changes. Everything that happens is written to `taskmaster.log`.


## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md): how Taskmaster is built and why: its
  scope, how the repository is laid out, its threads, and the life of a
  supervised program from start to stop.
- [CONTRACTS.md](docs/CONTRACTS.md): what the two halves of the program, the core
  and the shell, agreed on: the commands, the configuration keys and the errors.
- [CONTRIBUTING.md](CONTRIBUTING.md): how the two of us work together: branches,
  pull requests, reviews and commit messages.

## Resources

- [supervisor](http://supervisord.org/), the program Taskmaster is modelled on,
  and its control shell, `supervisorctl`.
- The Python documentation for
  [subprocess](https://docs.python.org/3/library/subprocess.html),
  [signal](https://docs.python.org/3/library/signal.html),
  [selectors](https://docs.python.org/3/library/selectors.html) and
  [readline](https://docs.python.org/3/library/readline.html).
- The [PyYAML documentation](https://pyyaml.org/wiki/PyYAMLDocumentation), for
  reading the configuration file.
