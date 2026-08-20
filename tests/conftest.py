"""Test fixtures.

Every test runs against a real git repository in a temporary directory. Nothing
here mocks git: a mocked subprocess would only prove that the mock behaves the
way we imagined, which is exactly the assumption that breaks in production.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from kiln.core.repository import Repository

GITATTRIBUTES = "*.blend lockable -text\n*.txt text\n"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True
    )
    return result.stdout.decode("utf-8", "replace")


def write_binary(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(marker.encode("utf-8").ljust(512, b"\0"))


def remove_tree(path: Path) -> None:
    """rmtree that copes with git's read-only object files on Windows."""

    def clear_read_only(func, failed_path, _exc_info):
        Path(failed_path).chmod(stat.S_IWRITE)
        func(failed_path)

    shutil.rmtree(path, onerror=clear_read_only)


@pytest.fixture
def bare_server(tmp_path: Path) -> Path:
    server = tmp_path / "server.git"
    server.mkdir()
    git(server, "init", "--bare", "--initial-branch=main")
    return server


@pytest.fixture
def workspace(tmp_path: Path, bare_server: Path) -> Path:
    """A clone with a few committed assets and an upstream branch."""
    clone = tmp_path / "workspace"
    git(tmp_path, "clone", str(bare_server), str(clone))
    git(clone, "config", "user.name", "Mira")
    git(clone, "config", "user.email", "mira@example.test")

    (clone / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    write_binary(clone / "chars/hero/hero.blend", "base hero")
    write_binary(clone / "chars/hero/tex/hero_color.blend", "base texture")
    write_binary(clone / "props/crate.blend", "base crate")
    (clone / "docs/readme.txt").parent.mkdir(parents=True, exist_ok=True)
    (clone / "docs/readme.txt").write_text("base notes\n", encoding="utf-8")

    git(clone, "add", "-A")
    git(clone, "commit", "-m", "Initial import")
    git(clone, "push", "-u", "origin", "main")
    return clone


@pytest.fixture
def repository(workspace: Path) -> Repository:
    return Repository(workspace)


@pytest.fixture
def second_clone(tmp_path: Path, bare_server: Path, workspace: Path) -> Path:
    """A second artist's clone of the same server."""
    other = tmp_path / "other"
    git(tmp_path, "clone", str(bare_server), str(other))
    git(other, "config", "user.name", "Devin")
    git(other, "config", "user.email", "devin@example.test")
    return other
