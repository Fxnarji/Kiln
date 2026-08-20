# Kiln — prototype

A desktop Git client for artists working on large binary assets. Git + Git LFS +
LFS file locking, against a self-hosted Gitea/Forgejo server.

See [SPEC-v1.md](SPEC-v1.md) for what this is meant to become. This repository
is the working prototype of milestones M0–M2 of that plan, plus the conflict
resolution screen.

## Running it

The entry point is **`run_kiln.py`**, which calls `main()` in `kiln/ui/app.py`.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install PySide6 pytest      # Linux: .venv/bin/python

# build a throwaway project to point it at
.venv/Scripts/python scripts/make_fixture_repo.py --into ../kiln-fixture

.venv/Scripts/python run_kiln.py ../kiln-fixture/workspace
```

Passing no path opens a folder picker.

To see the conflict screen, build the fixture mid-merge:

```bash
.venv/Scripts/python scripts/make_fixture_repo.py --into ../kiln-conflict --conflict --force
.venv/Scripts/python run_kiln.py ../kiln-conflict/workspace
```

Requires `git` and `git-lfs` on PATH. Kiln refuses to start without them.

## Building a distributable

```bash
.venv/Scripts/python -m pip install pyinstaller
.venv/Scripts/python -m PyInstaller --noconfirm --clean kiln.spec
```

The result is `dist/Kiln/`, containing `Kiln.exe` and its libraries. Ship the
whole folder — zip it and put it on the internal server.

Build decisions, all in `kiln.spec` with the reasoning next to them:

- **One-folder, not one-file.** One-file unpacks to a temp directory on every
  launch: slower to start, and the pattern Windows antivirus objects to most.
- **git and git-lfs are not bundled.** Kiln finds them on PATH and refuses to
  start without them. Bundling would mean owning their security updates.
- **No UPX compression.** A reliable way to get flagged by antivirus.
- **Windowed (`console=False`).** Set it to `True` in `kiln.spec` while
  debugging a packaged build, otherwise startup errors are invisible. Normal
  logging goes to the log file and the Diagnostics panel regardless.

Building on Linux uses the same spec file and produces a Linux binary. There is
no cross-compilation: build each platform on that platform.

## Tests

```bash
.venv/Scripts/python -m pytest
```

Tests run against real temporary git repositories. Nothing mocks git — a mocked
subprocess only proves the mock behaves as imagined, which is exactly the
assumption that breaks in production.

## Layout

```
kiln/git/     subprocess wrapper over git and git-lfs. Parsers for
              porcelain v2, lfs locks --json, and the commit log.
kiln/core/    repository state and policy. Pure Python: no Qt, no
              OS-specific calls. This is where the safety rules live.
kiln/ui/      PySide6 widgets. Never calls git directly — everything
              goes through the single background worker.
kiln/cli.py   `kiln lock`, `kiln unlock`, `kiln status`. The interface the
              Blender addon will use, so locking logic is never duplicated.
```

**`kiln/core` and `kiln/git` import no Qt.** They can be driven from a terminal
and tested headless. When something breaks at the studio it will nearly always
break in those two layers, and reproducing it should take ten seconds.

## What works

- Browsing the working tree, pinned folders with a recursion depth
- File status, size, lock holder, and download state
- Locking and releasing, with the open-for-editing sequence (spec 9.4)
- Commit, push, pull
- Discarding changes, with a backup in `.kiln/trash` first
- Whole-file conflict resolution, with both versions saved to
  `.kiln/conflicts` before anything is overwritten
- Refusing to act when the repository is in a state Kiln does not understand
- Diagnostics panel showing the last 20 git commands verbatim

## What does not

- **Locking needs a real Gitea/Forgejo server.** The fixture repo has no lock
  server, so Kiln shows locks as unavailable — which is worth seeing, since it
  is exactly what artists get when the server is down.
- No clone screen yet; open an existing clone.
- No thumbnails, no preview, no branch switching. All deliberate — see the
  non-goals in the spec.

## Notable deviations from the spec

- File detail is a panel beside the table rather than a separate screen. At this
  repository size an artist picks a file and immediately acts on it, so keeping
  both visible is better than swapping screens.
- The browse and changes screens share one table widget, filtered differently,
  rather than being separate views.
