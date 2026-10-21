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

You need Linux, Python 3.9 or newer, and `make`. On Debian or Ubuntu, Python
also needs a package to create virtual environments:
`sudo apt install python3-venv`.

```bash
make install                         # create .venv and install PyYAML in it
make run CONFIG=path/to/config.yaml  # start Taskmaster with your config file
make test                            # run the tests
make clean                           # delete .venv, caches and taskmaster.log
```

Everything is installed inside the project folder, in `.venv`, so the Python
on your system is left untouched.

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
