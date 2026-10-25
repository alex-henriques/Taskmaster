from __future__ import annotations

from dataclasses import dataclass

from taskmaster.core.config import ProgramConfig


@dataclass(frozen=True)
class Plan:
    """What a reload does to each program; names sorted unchanged ones are left out."""

    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]


def diff(target: dict[str, ProgramConfig], new: dict[str, ProgramConfig]) -> Plan:
    """Compare two configs program by program; ProgramConfig == decides "changed"."""
    both = target.keys() & new.keys()
    return Plan(
        added=tuple(sorted(new.keys() - target.keys())),
        changed=tuple(sorted(name for name in both if target[name] != new[name])),
        removed=tuple(sorted(target.keys() - new.keys())),
    )
