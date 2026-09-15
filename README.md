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
.venv/Scripts/python -m pip install -r requirements.txt   # Linux: .venv/bin/python

# build a throwaway project to point it at
.venv/Scripts/python scripts/make_fixture_repo.py

.venv/Scripts/python run_kiln.py .fixtures/project/workspace
```

Passing no path opens a folder picker.

Fixtures are written to `.fixtures/` inside the project, which is gitignored.
`--into` still accepts any path if you want one elsewhere.

To see the conflict screen, build the fixture mid-merge:

```bash
.venv/Scripts/python scripts/make_fixture_repo.py --conflict --force
.venv/Scripts/python run_kiln.py .fixtures/conflict/workspace
```

Requires `git` and `git-lfs` on PATH. Kiln refuses to start without them.

## Building a distributable

```bash
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/build_release.py
```

That produces two artifacts in `dist/`, either of which is safe to hand to
someone else:

| Artifact | Notes |
|---|---|
| `Kiln-<version>-windows.zip` | The one-folder build, zipped. Extract the whole folder, run `Kiln.exe` inside it. Starts in ~0.6 s. |
| `Kiln-<version>-windows.exe` | Single file, nothing to extract. Starts in ~1.5 s — it unpacks to a temp directory on every launch. |

**Never send `dist/Kiln/Kiln.exe` on its own.** In the one-folder build that is
a 2 MB launcher which cannot start without the 110 MB `_internal` folder beside
it. Sent alone it fails on the other machine with:

```
Failed to load Python DLL '...\_internal\python311.dll'
```

That cannot be caught from inside the application — it happens before Python
starts — which is why `build_release.py` exists and why the loose launcher is
not one of the artifacts it offers.

Build decisions, all in `kiln.spec` with the reasoning next to them:

- **Both targets share one Analysis**, so building them together costs far less
  than two separate builds.
- **git and git-lfs are not bundled.** Kiln finds them on PATH and refuses to
  start without them. Bundling would mean owning their security updates.
- **No UPX compression.** A reliable way to get flagged by antivirus.
- **Windowed (`console=False`).** Set it to `True` in `kiln.spec` while
  debugging a packaged build, otherwise startup errors are invisible. Normal
  logging goes to the log file and the Diagnostics panel regardless.

Building on Linux uses the same spec file and produces Linux binaries. There is
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
kiln/cli.py   DEPRECATED debugging aid. Not an integration boundary —
              the Blender addon calls `git lfs lock` directly (SPEC 7.5).
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
- Grid thumbnails read straight out of `.blend` files, including the
  Zstandard-compressed ones current Blender writes by default

## What does not

- **Locking needs a real Gitea/Forgejo server.** The fixture repo has no lock
  server, so Kiln shows locks as unavailable — which is worth seeing, since it
  is exactly what artists get when the server is down.
- No clone screen yet; open an existing clone.
- No branch switching. Deliberate — see the non-goals in the spec.
- Thumbnails only come from `.blend` files, and only when the artist has
  "Save Preview Images" enabled in Blender's preferences. Everything else
  gets a colour-coded placeholder tile.

## Notable deviations from the spec

- File detail is a panel beside the table rather than a separate screen. At this
  repository size an artist picks a file and immediately acts on it, so keeping
  both visible is better than swapping screens.
- The browse and changes screens share one table widget, filtered differently,
  rather than being separate views.
