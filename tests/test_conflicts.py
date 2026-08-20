"""Conflict resolution (spec 9.7).

The property these tests exist to protect: **no version is ever destroyed
without a copy on disk first**. "Keep server version" throws away an artist's
work, and that has to be recoverable.
"""

from __future__ import annotations

from pathlib import Path

from kiln.core import conflicts
from kiln.core.repository import Repository
from tests.conftest import git, write_binary


def make_conflict(repository: Repository, other_clone: Path, path: str) -> None:
    """Both artists edit the same file; ours is committed, theirs is pushed."""
    write_binary(other_clone / path, "SERVER version")
    git(other_clone, "add", "--", path)
    git(other_clone, "commit", "-m", "Their edit")
    git(other_clone, "push")

    write_binary(repository.root / path, "MY version")
    git(repository.root, "add", "--", path)
    git(repository.root, "commit", "-m", "My edit")

    git(repository.root, "fetch", "origin")
    repository.start_merge_with_server()


def test_pull_reports_divergence_rather_than_merging(
    repository: Repository, second_clone: Path
):
    path = "chars/hero/hero.blend"
    write_binary(second_clone / path, "SERVER version")
    git(second_clone, "add", "--", path)
    git(second_clone, "commit", "-m", "Their edit")
    git(second_clone, "push")

    write_binary(repository.root / path, "MY version")
    git(repository.root, "add", "--", path)
    git(repository.root, "commit", "-m", "My edit")

    result = repository.pull()

    assert not result.ok
    assert result.diverged
    assert not repository.snapshot(include_locks=False).merge_in_progress


def test_binary_conflict_is_offered_a_pick(repository: Repository, second_clone: Path):
    make_conflict(repository, second_clone, "chars/hero/hero.blend")

    found = repository.list_conflicts()

    assert [conflict.path for conflict in found] == ["chars/hero/hero.blend"]
    assert found[0].resolvable


def test_text_conflict_is_refused_with_a_reason(
    repository: Repository, second_clone: Path
):
    """A whole-file pick on text can silently drop a colleague's edits."""
    path = "docs/readme.txt"
    (second_clone / path).write_text("their notes\n", encoding="utf-8")
    git(second_clone, "add", "--", path)
    git(second_clone, "commit", "-m", "Their notes")
    git(second_clone, "push")

    (repository.root / path).write_text("my notes\n", encoding="utf-8")
    git(repository.root, "add", "--", path)
    git(repository.root, "commit", "-m", "My notes")
    git(repository.root, "fetch", "origin")
    repository.start_merge_with_server()

    found = repository.list_conflicts()

    assert not found[0].resolvable
    assert "text" in found[0].reason.lower()


def test_keeping_server_version_still_saves_mine(
    repository: Repository, second_clone: Path
):
    path = "chars/hero/hero.blend"
    make_conflict(repository, second_clone, path)

    resolution = repository.resolve_conflict(path, conflicts.SIDE_SERVER)

    assert b"SERVER version" in (repository.root / path).read_bytes()
    assert resolution.backup_mine is not None
    assert b"MY version" in resolution.backup_mine.read_bytes()


def test_keeping_my_version_still_saves_the_server_copy(
    repository: Repository, second_clone: Path
):
    path = "chars/hero/hero.blend"
    make_conflict(repository, second_clone, path)

    resolution = repository.resolve_conflict(path, conflicts.SIDE_MINE)

    assert b"MY version" in (repository.root / path).read_bytes()
    assert resolution.backup_server is not None
    assert b"SERVER version" in resolution.backup_server.read_bytes()


def test_both_backups_land_in_the_same_folder(
    repository: Repository, second_clone: Path
):
    """Side by side, so recovering the other version is obvious."""
    path = "chars/hero/hero.blend"
    make_conflict(repository, second_clone, path)

    resolution = repository.resolve_conflict(path, conflicts.SIDE_SERVER)

    mine_root = resolution.backup_mine.parents[len(Path(path).parts)]
    server_root = resolution.backup_server.parents[len(Path(path).parts)]
    assert mine_root.parent == server_root.parent


def test_completing_the_merge_clears_the_conflict(
    repository: Repository, second_clone: Path
):
    path = "chars/hero/hero.blend"
    make_conflict(repository, second_clone, path)
    repository.resolve_conflict(path, conflicts.SIDE_MINE)

    repository.complete_merge({path: conflicts.SIDE_MINE})

    state = repository.snapshot(include_locks=False)
    assert not state.merge_in_progress
    assert state.conflicted_files == []


def test_abandoning_the_merge_restores_my_version(
    repository: Repository, second_clone: Path
):
    path = "chars/hero/hero.blend"
    make_conflict(repository, second_clone, path)

    repository.abandon_merge()

    state = repository.snapshot(include_locks=False)
    assert not state.merge_in_progress
    assert b"MY version" in (repository.root / path).read_bytes()


def test_merge_message_records_which_side_won():
    from kiln.git.merge import SIDE_MINE, SIDE_SERVER, merge_message

    message = merge_message(
        {"a.blend": SIDE_MINE, "b.blend": SIDE_SERVER}, "origin/main"
    )

    assert "a.blend: kept my version" in message
    assert "b.blend: kept server version" in message
