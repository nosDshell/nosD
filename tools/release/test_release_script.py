#!/usr/bin/env python3
"""Guard the release helper against version-format regressions."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = REPO_ROOT / "packages/nosdshell/Scripts/dev/release.sh"

VERSION_QML = """pragma Singleton

import QtQuick
import Quickshell

Singleton {
  readonly property string baseVersion: "1.7.7.3"
  readonly property bool isDevelopment: true
  readonly property string developmentSuffix: "-git"
  readonly property string currentVersion: `v${!isDevelopment ? baseVersion : baseVersion + developmentSuffix}`
}
"""

CHANGELOG = """# Changelog

## [Unreleased]

Test release.

## [1.7.7.3] - 2026-11-17

Previous release.

[Unreleased]: https://github.com/nosDshell/nosD/compare/nosdshell-v1.7.7.3...HEAD
"""


class ReleaseScriptVersionTest(unittest.TestCase):
    def make_repo(self) -> tuple[Path, dict[str, str]]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        script = root / "Scripts/dev/release.sh"
        script.parent.mkdir(parents=True)
        shutil.copy2(RELEASE_SCRIPT, script)
        (root / "Commons").mkdir()
        (root / "Commons/Version.qml").write_text(VERSION_QML, encoding="utf-8")
        (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "Release Test",
                "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
                "GIT_COMMITTER_NAME": "Release Test",
                "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
            }
        )
        self.git(root, env, "init", "-q", "-b", "main")
        self.git(root, env, "config", "commit.gpgsign", "false")
        self.git(root, env, "config", "tag.gpgsign", "false")
        self.git(root, env, "add", ".")
        self.git(root, env, "commit", "-q", "-m", "test: baseline")
        self.git(
            root, env, "tag", "-a", "nosdshell-v1.7.7.3", "-m", "nosdshell-v1.7.7.3"
        )
        return root, env

    @staticmethod
    def git(root: Path, env: dict[str, str], *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_accepts_three_and_four_part_versions(self) -> None:
        for version in ("1.7.8", "1.7.7.4"):
            with self.subTest(version=version):
                root, env = self.make_repo()
                result = subprocess.run(
                    [
                        str(root / "Scripts/dev/release.sh"),
                        version,
                        "--no-push",
                        "--skip-verify",
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                version_qml = (root / "Commons/Version.qml").read_text()
                self.assertIn(f'baseVersion: "{version}"', version_qml)
                self.assertIn("isDevelopment: false", version_qml)
                changelog = (root / "CHANGELOG.md").read_text()
                self.assertIn(f"## [{version}] - ", changelog)
                self.assertEqual(
                    self.git(root, env, "rev-parse", "HEAD"),
                    self.git(root, env, "rev-parse", f"nosdshell-v{version}^{{}}"),
                )

    def test_rejects_non_numeric_versions(self) -> None:
        root, env = self.make_repo()
        result = subprocess.run(
            [
                str(root / "Scripts/dev/release.sh"),
                "1.7.7-rc1",
                "--no-push",
                "--skip-verify",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: release.sh <version>", result.stderr)


if __name__ == "__main__":
    unittest.main()
