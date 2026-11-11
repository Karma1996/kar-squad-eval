"""The KAR-SIM-Human CLI wrapper: profile injection and the agent-token guard."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "multica-sim-human"


def run(args, env_extra, fake_cli=None):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MULTICA_TOKEN", "MULTICA_TASK_ID", "KAR_SIM_CLI", "KAR_SIM_PROFILE"}
    }
    env.update(env_extra)
    if fake_cli is not None:
        env["KAR_SIM_CLI"] = str(fake_cli)
    return subprocess.run(
        [str(WRAPPER), *args], capture_output=True, text=True, env=env, check=False
    )


class WrapperTest(unittest.TestCase):
    def test_wrapper_is_executable(self):
        self.assertTrue(WRAPPER.is_file(), "scripts/multica-sim-human is missing")
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "wrapper must be executable")

    def test_refuses_an_agent_task_token(self):
        completed = run(
            ["issue", "get", "KAR-1"],
            {"MULTICA_TOKEN": "mat_fake", "KAR_SIM_CLI": "/bin/echo"},
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("MULTICA_TOKEN", completed.stderr)
        self.assertEqual(completed.stdout, "", "must not fall through to the CLI")

    def test_injects_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake-cli"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
            fake.chmod(0o755)
            completed = run(["issue", "get", "KAR-1"], {}, fake_cli=fake)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.split()
        self.assertEqual(lines[:3], ["--profile", "kar-sim-human", "issue"])
        self.assertEqual(lines[3:], ["get", "KAR-1"])

    def test_profile_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake-cli"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
            fake.chmod(0o755)
            completed = run(
                ["--version"], {"KAR_SIM_PROFILE": "probe"}, fake_cli=fake
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.split()[:2], ["--profile", "probe"])


if __name__ == "__main__":
    unittest.main()
