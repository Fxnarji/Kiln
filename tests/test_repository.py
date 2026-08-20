"""Repository behaviour against real git repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln.core.models import Pin
from kiln.core.repository import Repository
from kiln.errors import RepositoryBusyError
from tests.conftest import git, write_binary


def test_snapshot_lists_tracked_files(repository: Repository):
    state = repository.snapshot(include_locks=False)
    paths = {entry.path for entry in state.files}

    assert "chars/hero/hero.blend" in paths
    assert "props/crate.blend" in paths
    assert state.branch == "main"
    assert not state.blocked_reason


def test_snapshot_reports_modified_and_new(repository: Repository):
    write_binary(repository.root / "chars/hero/hero.blend", "edited hero")
    write_binary(repository.root / "props/barrel.blend", "brand new")

    state = repository.snapshot(include_locks=False)
    statuses = {entry.path: entry.status for entry in state.changed_files}

    assert statuses["chars/hero/hero.blend"] == "modified"
    assert statuses["props/barrel.blend"] == "new"


def test_commit_only_includes_selected_files(repository: Repository):
    write_binary(repository.root / "chars/hero/hero.blend", "edited hero")
    write_binary(repository.root / "props/crate.blend", "edited crate")

    repository.commit("Hero pass", ["chars/hero/hero.blend"])

    remaining = {entry.path for entry in repository.snapshot(include_locks=False).changed_files}
    assert remaining == {"props/crate.blend"}


def test_commit_requires_a_message(repository: Repository):
    with pytest.raises(ValueError):
        repository.commit("   ", ["props/crate.blend"])


def test_discard_backs_the_file_up_before_destroying_it(repository: Repository):
    target = "chars/hero/hero.blend"
    write_binary(repository.root / target, "work I am about to throw away")

    backup = repository.discard(target)

    assert backup is not None and backup.is_file()
    assert b"work I am about to throw away" in backup.read_bytes()
    assert b"base hero" in (repository.root / target).read_bytes()


def test_discard_of_a_new_file_removes_it_but_keeps_a_copy(repository: Repository):
    target = "props/experiment.blend"
    write_binary(repository.root / target, "never committed")

    backup = repository.discard(target)

    assert not (repository.root / target).exists()
    assert backup is not None and b"never committed" in backup.read_bytes()


def test_kiln_directory_is_excluded_from_git(repository: Repository):
    repository.discard("chars/hero/hero.blend")  # creates .kiln/

    untracked = {
        entry.path
        for entry in repository.snapshot(include_locks=False).files
        if entry.path.startswith(".kiln")
    }
    assert untracked == set()


def test_detached_head_blocks_writing(repository: Repository):
    head = git(repository.root, "rev-parse", "HEAD").strip()
    git(repository.root, "checkout", "--detach", head)

    state = repository.snapshot(include_locks=False)
    assert state.blocked_reason
    assert not state.can_write

    with pytest.raises(RepositoryBusyError):
        repository.commit("anything", ["props/crate.blend"])


def test_folders_lists_every_level(repository: Repository):
    folders = repository.folders()

    assert "chars" in folders
    assert "chars/hero" in folders
    assert "chars/hero/tex" in folders


def test_add_folder_includes_empty_folder(repository: Repository):
    repository.add_folder("new-assets")

    assert (repository.root / "new-assets").is_dir()
    assert "new-assets" in repository.folders()


def test_add_from_template_copies_and_stages_one_file(repository: Repository):
    template = repository.root / "_templates" / "512x512.psd"
    template.parent.mkdir()
    template.write_bytes(b"template")

    target = repository.add_from_template(template, "props/icon.psd")

    assert target.read_bytes() == b"template"
    assert "props/icon.psd" in git(repository.root, "diff", "--cached", "--name-only")


def test_delete_to_trash_can_be_undone(repository: Repository):
    target = "props/crate.blend"
    original = (repository.root / target).read_bytes()

    deleted = repository.delete_to_trash(target)
    assert not (repository.root / target).exists()
    assert deleted.backup_path.is_file()

    repository.undo_delete(deleted)
    assert (repository.root / target).read_bytes() == original


def test_snapshot_without_locks_does_not_touch_the_server(repository: Repository):
    """Offline browsing must still work (spec 10.3)."""
    state = repository.snapshot(include_locks=False)

    assert state.files
    assert state.locks_fetched_at is None


class TestPinDepth:
    """Depth is the one novel navigation idea, so its edges are pinned down."""

    def test_depth_one_is_the_folder_itself(self):
        pin = Pin(path="chars", depth=1)

        assert pin.contains("chars/notes.blend")
        assert not pin.contains("chars/hero/hero.blend")

    def test_depth_two_includes_one_subfolder_level(self):
        pin = Pin(path="chars", depth=2)

        assert pin.contains("chars/hero/hero.blend")
        assert not pin.contains("chars/hero/tex/hero_color.blend")

    def test_unlimited_depth_includes_everything(self):
        pin = Pin(path="chars", depth=5)

        assert pin.contains("chars/hero/tex/deep/deeper/file.blend")

    def test_pin_excludes_other_folders(self):
        pin = Pin(path="chars", depth=5)

        assert not pin.contains("props/crate.blend")

    def test_similar_folder_name_is_not_a_match(self):
        pin = Pin(path="chars", depth=5)

        assert not pin.contains("chars_old/hero.blend")

    def test_root_pin_covers_the_whole_repository(self):
        pin = Pin(path="", depth=5)

        assert pin.contains("chars/hero/hero.blend")


def test_pins_persist_per_repository(repository: Repository, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    repository.add_pin(Pin(path="chars", depth=2, label="Characters"))
    reopened = Repository(repository.root)

    assert [pin.path for pin in reopened.pins()] == ["chars"]
