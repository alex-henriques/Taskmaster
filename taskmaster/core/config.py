from __future__ import annotations

import math
import os
import re
import shlex
import signal
from dataclasses import dataclass

import yaml


class ConfigError(Exception):
    """The config file is invalid: str(e) says where and why."""


@dataclass(frozen=True)
class ProgramConfig:
    name: str
    cmd: tuple[str, ...]
    numprocs: int = 1
    umask: int | None = None
    workingdir: str | None = None
    autostart: bool = True
    autorestart: str = "unexpected"
    exitcodes: tuple[int, ...] = (0,)
    startretries: int = 3
    starttime: float = 1.0
    stopsignal: signal.Signals = signal.SIGTERM
    stoptime: float = 10.0
    stdout: str | None = None
    stderr: str | None = None
    env: tuple[tuple[str, str], ...] = ()

    def instance_names(self) -> list[str]:
        if self.numprocs == 1:
            return [self.name]
        return [f"{self.name}_{i:02d}" for i in range(self.numprocs)]


# Each check either returns the normalized value or raises an TypeError/ValueError("<problem>").
# type(v) is int, not isinstance: bool is a subclass of int.


def _cmd(value) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return tuple(shlex.split(value))


def _int(value, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"must be an integer >= {minimum}")
    return value


def _duration(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("must be a number >= 0")
    return float(value)


def _umask(value) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-7]+", value):
        value = int(value, 8)
    if type(value) is not int or not 0 <= value <= 0o777:
        raise ValueError("must be an octal number")
    return value


def _bool(value) -> bool:
    if type(value) is not bool:
        raise ValueError("must be true or false")
    return value


def _autorestart(value) -> str:
    if type(value) is bool:
        return "always" if value else "never"
    if value not in ("always", "never", "unexpected"):
        raise ValueError("must be always, never or unexpected")
    return value


def _exitcodes(value) -> tuple[int, ...]:
    codes = value if isinstance(value, list) else [value]
    if not all(type(code) is int for code in codes):
        raise ValueError("must be an integer or a list of integers")
    return tuple(codes)


def _signal(value) -> signal.Signals:
    try:
        return signal.Signals["SIG" + str(value).removeprefix("SIG")]
    except KeyError:
        raise ValueError(f"unknown signal {value}") from None


def _path(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("must be a path")
    return value


def _env(value) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise TypeError("must be a map of NAME: value")
    for key, val in value.items():
        if not isinstance(key, str) or type(val) not in (str, int):
            raise ValueError(f"{key} must be a string or an int; put it in quotes")
    return tuple(sorted((key, str(val)) for key, val in value.items()))


_FIELDS = {
    "cmd": _cmd,
    "numprocs": lambda v: _int(v, 1),
    "umask": _umask,
    "workingdir": _path,
    "autostart": _bool,
    "autorestart": _autorestart,
    "exitcodes": _exitcodes,
    "startretries": lambda v: _int(v, 0),
    "starttime": _duration,
    "stopsignal": _signal,
    "stoptime": _duration,
    "stdout": _path,
    "stderr": _path,
    "env": _env,
}
_PATHS = ("workingdir", "stdout", "stderr")
_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _program(name: str, raw, base: str) -> ProgramConfig:
    """Build one ProgramConfig from its YAML block; the first bad field raises ConfigError"""
    where = f"programs.{name}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a map of fields")
    values = {}
    for field, value in raw.items():
        if field not in _FIELDS:
            raise ConfigError(f"{where}.{field}: unknown field")
        try:
            values[field] = _FIELDS[field](value)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{where}.{field}: {e}") from None
    if "cmd" not in values:
        raise ConfigError(f"{where}.cmd: is required")
    for field in _PATHS:
        if field in values:
            values[field] = os.path.join(base, values[field])
    return ProgramConfig(name, **values)


def load(path: str) -> dict[str, ProgramConfig]:
    """Read and validate the config file; raises ConfigError on the first problem found."""
    try:
        with open(path, "rb") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(str(e)) from None
    if (
        not isinstance(data, dict)
        or data.keys() != {"programs"}
        or not isinstance(data["programs"], dict)
    ):
        raise ConfigError(f"{path}: needs one top-level key, programs, holding a map")
    base = os.path.dirname(os.path.abspath(path))
    configs: dict[str, ProgramConfig] = {}
    taken: set[str] = set()
    for name, raw in data["programs"].items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name == "all":
            raise ConfigError(
                f"programs.{name}: a name uses only A-Z, a-z, 0-9, _ or - and is not all"
            )
        config = _program(name, raw, base)
        clash = taken.intersection(config.instance_names())
        if clash:
            raise ConfigError(f"programs.{name}: instance {min(clash)} already exists")
        taken.update(config.instance_names())
        configs[name] = config
    return configs
