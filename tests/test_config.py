from __future__ import annotations

import os
import signal
import tempfile
import unittest

from taskmaster.core.config import ConfigError, ProgramConfig, load

SUBJECT = os.path.join(os.path.dirname(__file__), "data", "subject.yaml")


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def load_text(self, text: str) -> dict[str, ProgramConfig]:
        path = os.path.join(self.dir, "c.yaml")
        with open(path, "w") as f:
            f.write(text)
        return load(path)

    def test_subject_example(self) -> None:
        configs = load(SUBJECT)
        nginx, vogsphere = configs["nginx"], configs["vogsphere"]
        self.assertEqual(nginx.umask, 0o22)
        self.assertEqual(nginx.exitcodes, (0, 2))
        # ANSWER: 42 is a YAML int, converted with str()
        self.assertEqual(nginx.env, (("ANSWER", "42"), ("STARTED_BY", "taskmaster")))
        self.assertEqual(vogsphere.stopsignal, signal.SIGUSR1)
        names = vogsphere.instance_names()
        self.assertEqual(len(names), 8)
        self.assertEqual((names[0], names[-1]), ("vogsphere_00", "vogsphere_07"))

    def test_defaults(self) -> None:
        # written out from CONTRACTS §4, not from the dataclass defaults
        expected = ProgramConfig(
            name="a",
            cmd=("ls",),
            numprocs=1,
            umask=None,
            workingdir=None,
            autostart=True,
            autorestart="unexpected",
            exitcodes=(0,),
            startretries=3,
            starttime=1.0,
            stopsignal=signal.SIGTERM,
            stoptime=10.0,
            stdout=None,
            stderr=None,
            env=(),
        )
        self.assertEqual(self.load_text("programs: {a: {cmd: ls}}"), {"a": expected})

    def test_errors(self) -> None:
        cases = [
            ("programs: {web: {numprocs: 2}}", "programs.web.cmd"),
            ("programs: {web: {cmd: ls, stoptim: 3}}", "programs.web.stoptim"),
            ("programs: {web: {cmd: ls, stoptime: -1}}", "programs.web.stoptime"),
            (
                "programs: {web: {cmd: ls, autorestart: sometimes}}",
                "programs.web.autorestart",
            ),
            ("programs: {web: {cmd: ls, stopsignal: NOPE}}", "programs.web.stopsignal"),
            ("programs: {web: {cmd: ls, numprocs: 0}}", "programs.web.numprocs"),
            ("programs: {all: {cmd: ls}}", "programs.all"),
            ("programs: {web: {cmd: ls, env: {DEBUG: yes}}}", "programs.web.env"),
            ("programs: {web: {cmd: ls, env: {V: 3.10}}}", "programs.web.env"),
            ('programs: {web: {cmd: "echo \'oops"}}', "programs.web.cmd"),
            # refused as an unknown field until T14 replaces this case
            ("programs: {web: {cmd: ls, user: nobody}}", "programs.web.user"),
            (
                "programs: {web: {cmd: ls, numprocs: 2}, web_01: {cmd: ls}}",
                "programs.web_01",
            ),
            ("programs: {web: {cmd: ls, stoptime: .inf}}", "programs.web.stoptime"),
            ("programs: {web: {cmmd: ls}}", "programs.web.cmmd"),
        ]
        for text, prefix in cases:
            with self.subTest(text):
                with self.assertRaises(ConfigError) as cm:
                    self.load_text(text)
                self.assertTrue(str(cm.exception).startswith(prefix), cm.exception)

    def test_umask_forms(self) -> None:
        bare = self.load_text("programs: {a: {cmd: ls, umask: 022}}")["a"]
        quoted = self.load_text('programs: {a: {cmd: ls, umask: "022"}}')["a"]
        self.assertEqual((bare.umask, quoted.umask), (0o22, 0o22))

    def test_relative_paths(self) -> None:
        configs = self.load_text("programs: {a: {cmd: ls, stdout: logs/x.out}}")
        self.assertEqual(configs["a"].stdout, os.path.join(self.dir, "logs", "x.out"))

    def test_equality(self) -> None:
        first, second = load(SUBJECT), load(SUBJECT)
        self.assertEqual(first, second)
        before = self.load_text("programs: {a: {cmd: ls}, b: {cmd: ls}}")
        after = self.load_text("programs: {a: {cmd: ls}, b: {cmd: ls, stoptime: 11}}")
        self.assertEqual(before["a"], after["a"])
        self.assertNotEqual(before["b"], after["b"])

    def test_missing_file(self) -> None:
        path = os.path.join(self.dir, "nope.yaml")
        with self.assertRaises(ConfigError):
            load(path)

    def test_bad_file(self) -> None:
        for text in ("", "progams: {}", "programs:", "programs: [\n"):
            with self.subTest(text), self.assertRaises(ConfigError):
                self.load_text(text)
