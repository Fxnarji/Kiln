"""Parser tests pinned against real porcelain v2 output.

These exist so that a git version bump which changes the output format fails
here rather than in front of an artist.
"""

from __future__ import annotations

from kiln.git.status import parse_status

NUL = "\0"


def build(*records: str) -> str:
    return NUL.join(records) + NUL


def test_reads_branch_headers():
    raw = build(
        "# branch.oid abc123",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +3 -1",
    )
    report = parse_status(raw)

    assert report.branch == "main"
    assert report.head_oid == "abc123"
    assert report.upstream == "origin/main"
    assert report.ahead == 3
    assert report.behind == 1
    assert report.has_upstream


def test_no_upstream_leaves_counts_at_zero():
    report = parse_status(build("# branch.head main"))

    assert report.ahead == 0
    assert report.behind == 0
    assert not report.has_upstream


def test_reads_ordinary_changes():
    raw = build(
        "# branch.head main",
        "1 .M N... 100644 100644 100644 aaa bbb chars/hero.blend",
        "1 A. N... 000000 100644 100644 000 ccc chars/new.blend",
        "1 .D N... 100644 100644 000000 ddd eee chars/gone.blend",
    )
    statuses = {entry.path: entry.status for entry in parse_status(raw).entries}

    assert statuses["chars/hero.blend"] == "modified"
    assert statuses["chars/new.blend"] == "new"
    assert statuses["chars/gone.blend"] == "deleted"


def test_untracked_files_count_as_new():
    report = parse_status(build("# branch.head main", "? props/untracked.blend"))

    assert report.entries[0].path == "props/untracked.blend"
    assert report.entries[0].status == "new"


def test_ignored_files_are_dropped():
    report = parse_status(build("# branch.head main", "! build/output.blend"))

    assert report.entries == []


def test_paths_containing_spaces_survive():
    raw = build(
        "# branch.head main",
        "1 .M N... 100644 100644 100644 aaa bbb chars/my hero file.blend",
    )

    assert parse_status(raw).entries[0].path == "chars/my hero file.blend"


def test_rename_record_consumes_its_original_path():
    """A '2' record is followed by a second NUL-separated field.

    If that extra field were treated as its own record, every rename would add
    a phantom file to the list.
    """
    raw = build(
        "# branch.head main",
        "2 R. N... 100644 100644 100644 aaa bbb R100 chars/new_name.blend",
        "chars/old_name.blend",
        "1 .M N... 100644 100644 100644 ccc ddd props/crate.blend",
    )
    entries = parse_status(raw).entries

    assert [entry.path for entry in entries] == [
        "chars/new_name.blend",
        "props/crate.blend",
    ]


def test_unmerged_records_are_conflicted():
    raw = build(
        "# branch.head main",
        "u UU N... 100644 100644 100644 100644 aaa bbb ccc chars/hero.blend",
    )
    report = parse_status(raw)

    assert report.entries[0].status == "conflicted"
    assert report.conflicted_paths == ["chars/hero.blend"]


def test_malformed_record_is_skipped_not_fatal():
    raw = build("# branch.head main", "1 broken", "? fine.blend")
    report = parse_status(raw)

    assert [entry.path for entry in report.entries] == ["fine.blend"]
