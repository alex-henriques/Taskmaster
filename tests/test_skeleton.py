from __future__ import annotations

import importlib
import unittest


class SkeletonTest(unittest.TestCase):
    def test_packages_import(self) -> None:
        for name in ("taskmaster", "taskmaster.core", "taskmaster.shell"):
            with self.subTest(package=name):
                importlib.import_module(name)
