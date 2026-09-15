# Kiln v1 — Specification

Status: draft for review
Date: 2026-08-20

A desktop Git client for artists working on large binary assets, built on
Git + Git LFS + LFS file locking, against a self-hosted Gitea/Forgejo server.

---

## 1. Purpose

Let an artist who does not know git safely do five things:

1. Get the project onto their machine.
2. See what's in it and who is currently holding which file.
3. Open a file for work — which claims it, so nobody else edits it at the same time.
4. See what they changed, and commit and push it.
5. Pull everyone else's work.

Everything else is out of scope. Anything git can do that this list does not
cover is delegated to the command line, deliberately and permanently.

## 2. Decided constraints

| Constraint | Decision |
|---|---|
| Platform | Windows day 1. Linux immediately after. |
| OS coupling | The core contains **zero** Windows-specific code. Linux is a repackage, not a port. |
| Host | Self-hosted Gitea / Forgejo (implements the LFS locking API). |
| Stack | Python 3.11+ / PySide6 (Qt6 Widgets). |
| Audience | A real team on a real repo. People's work is at risk. |
| Visual priority | Function and maintainability first. Native Qt defaults everywhere. |
| Branching | One shared branch. No switching, ever. |
| Scale | 5 users, 2–3 concurrent. ~7 GB, 300–500 files, largest file under 1 GB. |
| Connectivity | Always-online assumed. Offline degrades to read-only with a warning. |

**The scale line drives more of this document than anything else.** 500 files is
small. `git status` is instant. Nothing needs to be clever. Every place where a
performance concern would normally appear, the answer here is "it doesn't matter,
do the simple thing."

## 3. Non-goals for v1

Explicitly not built, and not designed around:

- Branch creation, switching, or display beyond a name in the status bar.
- Content-level or three-way merging. Binary conflicts get a whole-file pick
  (9.7); text conflicts escalate to the CLI.
- Diff or image-comparison views.
- Real thumbnails for `.blend` / `.spp` / `.psd`.
- An "Open with" application registry (v1 uses the OS default handler).
- Stealing or force-releasing another person's lock.
- Auto-releasing a lock when a DCC application closes.
- Selective/partial clone. At 7 GB the whole repo is cloned. See 7.3.
- History rewriting, force-push recovery, stash, rebase, cherry-pick, reflog.
- Any host other than Gitea/Forgejo.
- Light theme, responsive layout, custom styling.

## 4. Stack decision and rationale

**Python 3.11+ with PySide6 (Qt6 Widgets).**

Chosen because the maintainer works in Python (bpy) and must be able to diagnose
and fix any failure alone, under time pressure. That requirement outranks every
other stack consideration.

- One language end to end. No build step, no bundler, no second debugger.
- Native Windows and Linux from identical source.
- Qt Widgets is stable, exhaustively documented, and 20 years old.
- Subprocess control is a first-class concern in Qt, and this application is
  fundamentally a subprocess driver.
- Python is also the language of the Blender-side integration (7.5), so core
  logic can be imported there directly if it ever needs to be.

Rejected: any web frontend (Electron/Tauri/pywebview) — adds a language and a
toolchain the maintainer cannot debug. QML/Qt Quick — prettier, but QML+JS is a
second language. Tkinter — would not survive the tree/table workload.

### 4.1 UI rules

These are binding, not preferences:

- Styling is `QApplication.setStyle("Fusion")` plus a dark `QPalette`. **No QSS**
  unless a specific widget cannot be made legible otherwise.
- System default fonts. No downloaded or bundled fonts.
- Item-based convenience widgets throughout: `QTreeWidget`, `QTableWidget`,
  `QListWidget`. At 500 files these never need promoting to a model/view.
- Standard Qt icons, or no icons. No custom iconography.
- If any visual detail fights the toolkit — a font, a shadow, a gradient, a
  rounded corner — drop it immediately and use the default. This is never a
  reason to spend a day.

## 5. Architecture

Four components. The dependency direction is strictly downward.

```
kiln/ui/        PySide6. Widgets, layout, event wiring.
                May not call git. May not touch the filesystem.

kiln/cli.py     DEPRECATED. A thin argparse entry point over core, kept
                as a debugging aid only. It is not an integration boundary
                and gets no further investment: anything it does, git does
                directly. See 7.5.

kiln/core/      Pure Python. Repo state, lock state, pin/depth logic,
                the open-for-edit sequence, LFS pointer detection,
                path handling (pathlib only).
                MUST NOT import PySide6. MUST NOT contain OS-specific code.

kiln/git/       Subprocess wrapper over the `git` and `git-lfs` binaries.
                Builds argv lists, parses machine-readable output,
                returns dataclasses. Never interprets policy.
```

**The hard rule: `kiln/core` and `kiln/git` import no Qt.** They are runnable and
testable headless, from pytest, against fixture repos, with no window on screen.
This is the single most important maintainability decision in the document: when
something breaks at the studio it will almost always break in these two layers,
and you must be able to reproduce it in a terminal in ten seconds. It is also
what lets the same logic be imported from Blender's Python if it is ever
needed there.

### 5.1 Threading

One UI thread. One worker thread. That is the entire concurrency model.

- Every git invocation runs on the worker via a `QThread` plus a worker object,
  and reports back with signals. No asyncio, no thread pools, no nested futures.
- The UI never blocks on git.
- Only one git operation runs at a time, serialized through a queue. Git takes a
  repository lock anyway; serializing in-process turns a confusing concurrent
  failure into an ordered one.

## 6. The git / LFS layer

Kiln shells out. It does not link libgit2 or gitoxide — neither supports LFS or
LFS locking, which are the two things this application is about.

Every command is invoked with an explicit argv list (never a shell string), an
explicit `cwd`, and a timeout.

### 6.1 Command inventory

| Purpose | Command |
|---|---|
| Working tree status | `git status --porcelain=v2 -z` |
| Ahead / behind | `git rev-list --left-right --count @{u}...HEAD` |
| Fetch (background) | `git fetch --prune` |
| Recent commits | `git log -n 50 --format=%H%x00%an%x00%at%x00%s` |
| File history | `git log -n 50 --format=<same> -- <path>` |
| Stage | `git add -- <path>` (never `-A`, never `.`) |
| Commit | `git commit -F -` with the message on **stdin** |
| Push / pull | `git push`, `git pull --ff-only` |
| List locks | `git lfs locks --json` |
| Take lock | `git lfs lock -- <path>` |
| Release lock | `git lfs unlock -- <path>` |
| Materialize a file | `git lfs checkout -- <path>` |
| Start a merge | `git merge <remote-branch>` |
| Take my version | `git checkout --ours -- <path>` |
| Take server version | `git checkout --theirs -- <path>` |
| Mark resolved | `git add -- <path>` |
| Abandon the merge | `git merge --abort` |

Message-on-stdin for commits is deliberate: it removes an entire class of quoting
and encoding bugs across Windows and Linux.

The exact JSON field names returned by `git lfs locks --json` must be confirmed
against the deployed Gitea version during M2 and pinned in a parser test.

### 6.2 Parsing

Only machine-readable formats: `--porcelain=v2`, `-z`, `--json`, and
NUL-delimited log formats. Human-facing git output is never parsed.

`git --version` and `git lfs version` are checked at startup and recorded in the
log. A minimum version is asserted; below it, Kiln refuses to start and says
which binary is too old.

## 7. Repository setup

### 7.1 `.gitattributes` — `lockable` is on

Decided: lockable is set. Committed to the repo, administered by the maintainer.
Kiln reads it and warns if an extension it is asked to lock is not covered, but
never silently rewrites it.

```
*.blend  filter=lfs diff=lfs merge=lfs -text lockable
*.spp    filter=lfs diff=lfs merge=lfs -text lockable
*.psd    filter=lfs diff=lfs merge=lfs -text lockable
```

`lockable` makes matching files **read-only on disk** until locked. This is the
only mechanism that actually prevents two artists editing the same file;
everything else is advisory UI. It is also the thing that makes an artist's DCC
refuse to save over someone else's work even if they bypassed Kiln entirely.

Two operational notes:

- The read-only bit is applied at checkout. After adding `lockable` to an
  existing repo, everyone needs a fresh checkout of those paths for it to take
  effect. This is a one-time migration step, not a Kiln feature.
- `git lfs lock` is expected to make the file writable and `git lfs unlock` to
  restore read-only. Kiln does not assume this: after locking it **verifies the
  file is writable**, and applies the permission change itself if not. That
  fallback is the one place OS-specific behavior may leak in — it lives behind a
  single function in `kiln/core` with a pathlib/`os.chmod` implementation and a
  test on both platforms.

### 7.2 Local config Kiln applies on clone

Nothing. At 500 files no status tuning is needed — no `fsmonitor`, no
`untrackedCache`, no LFS fetch filters. Config that isn't set can't be wrong.

### 7.3 Clone downloads everything

7 GB total. Kiln clones the whole repository with all LFS objects. The mockup's
folder-selection screen is cut from v1.

Rationale: it saves a one-time download on a repo small enough that the download
is not a problem, in exchange for a permanent class of "why is this file
missing / why is it a text file / why did Blender fail to open it" support
questions. That trade is wrong at this size. It becomes right somewhere north of
50–100 GB, at which point `lfs.fetchinclude` can be applied to existing clones
without any migration — so nothing is lost by waiting.

Pointer detection is still implemented in `kiln/core`, because a *failed* or
interrupted LFS pull leaves pointer files on disk and the artist must be able to
see that. A file whose contents begin with
`version https://git-lfs.github.com/spec/v1` is not downloaded. Kiln shows this
state clearly and offers a re-download, and refuses to open such a file until it
is materialized.

### 7.4 Authentication

Kiln does not store, prompt for, or manage credentials. It relies on the system
git credential helper and/or SSH keys, configured once outside Kiln. If a remote
operation fails on auth, Kiln says so plainly and points at the setup doc.

### 7.5 Integration surface for outside-Kiln edits

Kiln cannot intercept every way a file gets opened. The policy is:

- **Files opened through Kiln** — Kiln locks them (9.4).
- **Files opened from within an application** (Blender opening a linked
  reference, a texture, a library file) — handled by an in-app extension that
  calls Kiln. In Blender's case, a bpy addon.
- **Everything else** — handled manually by the artist. Documented, accepted.

**The addon calls `git lfs lock` directly.** Not Kiln.

This reverses an earlier decision in this document, and the reasoning is worth
keeping because it will come up again. The original plan was for the addon to
shell out to a Kiln CLI, so that "locking logic is never duplicated". Setting
the two side by side, the logic being protected turns out to be thin:

| Step in Kiln's open sequence | What git already guarantees |
|---|---|
| Re-query the server before locking | `git lfs lock` is itself authoritative |
| Refuse if somebody else holds it | `git lfs lock` exits non-zero, saying who |
| Take the lock | the same command |
| Make the file writable | git-lfs does this for `lockable` files |
| Materialise an LFS pointer first | one extra `git lfs pull --include=` |

What is left is roughly two conveniences, which is not enough to justify
shipping a second binary, matching its version to the addon, and putting a
process boundary in the middle of opening a file.

The deciding argument is 10.6: **Kiln is never required.** An addon that can
only lock by calling Kiln makes Kiln required for a second workflow, so a
broken Kiln would also break Blender. Calling git directly keeps that promise.

If the open sequence later grows real policy — settling Q9, say, so that
opening a file behind the remote is refused — the answer still is not a
subprocess boundary. It is for the addon to import `kiln.core.editing` from
Blender's own Python. That module is free of Qt and of OS-specific calls
precisely so it can be imported anywhere, which also avoids ~400 ms of
interpreter startup per call. That matters if a scene load locks twenty linked
files at once.

The addon itself remains **out of scope for v1**.

## 8. Core data model

```python
@dataclass(frozen=True)
class Pin:            # a saved folder view
    path: str         # repo-relative, e.g. "chars"
    depth: int        # 1..5, where 5 means unlimited
    label: str

@dataclass(frozen=True)
class Lock:
    path: str
    owner: str        # email/username from the server
    locked_at: datetime
    is_mine: bool

@dataclass(frozen=True)
class FileEntry:
    path: str
    status: Literal["clean", "modified", "new", "deleted"]
    downloaded: bool  # False means an LFS pointer is on disk
    writable: bool    # tracks the lockable read-only bit
    size: int | None
    lock: Lock | None

@dataclass(frozen=True)
class RepoState:
    branch: str
    head: str
    ahead: int
    behind: int
    clean_state: bool            # False if mid-merge, detached, conflicted — see 10.5
    online: bool
    files: list[FileEntry]
    locks_fetched_at: datetime   # see 10.2
```

`RepoState` is rebuilt wholesale on every refresh and handed to the UI as an
immutable snapshot. No incremental mutation of UI state. At 500 files a full
rebuild is free, and it eliminates the entire class of bugs where the display
disagrees with the disk.

Pins are stored per-clone in a JSON file under the platform config dir (via
`platformdirs`), keyed by repo path. Pins are local preference, not repo data.

Pins with depth are kept — they are cheap and they are the one genuinely novel
piece of navigation here — but note that at 300–500 files they are a convenience,
not a necessity. If the depth selector proves confusing in M1 dogfooding, reduce
it to a single "include subfolders" checkbox and move on.

## 9. Screens

Six. Every one is a plain Qt layout.

### 9.1 Main window

`QMainWindow`. A `QSplitter` with the sidebar left and a `QStackedWidget` right.
The status bar shows: branch name, ahead/behind counts, lock-freshness age,
online/offline, and the currently running operation.

Toolbar: `Pull`, `Push`, `Refresh`. Nothing else.

### 9.2 Sidebar

A single `QTreeWidget` with three top-level sections:

- **Pinned folders** — saved views. Selecting one opens the Browse screen.
- **Working tree** — the repo tree. Right-click a folder to pin it.
- **Repository** — Changes (n), My locks (n).

### 9.3 Browse

A sortable `QTableWidget`, replacing the mockup's thumbnail grid. Columns:

Name | Path | Size | State | Lock | Writable

A filter row above it: text filter, plus checkboxes for *locked only* and
*changed only*. The pin's depth controls how deep the listing recurses.

Double-click selects and shows detail. It does **not** open and does **not**
lock — opening is an explicit button, because opening takes a lock.

### 9.4 File detail — and the Open sequence

Left: a properties block — full path, size, modified state, download state,
writable state, current lock holder and age.

Right: `Open for editing`, `Release lock`, `Discard changes`, and a
`QTableWidget` of the file's last 50 commits.

**`Open for editing` runs a fixed, ordered sequence.** This is the most
important flow in the application:

1. If the file is a pointer, materialize it (`git lfs pull --include=<path>`).
   Fail here and stop — never hand a DCC a pointer file.
2. Re-query locks from the server (not the cache).
3. If someone else holds it → refused-lock dialog (9.6). Stop.
4. `git lfs lock -- <path>`. If this fails, stop and report. **Do not launch.**
5. Verify the file is writable. If not, apply the permission change (7.1).
6. Launch via the OS default handler (`QDesktopServices.openUrl`).

Locking before launching, and refusing to launch if locking failed, is what makes
this safe. The artist never ends up editing a file they don't hold.

An `Open read-only` action exists alongside it, which skips steps 2–5.

### 9.5 Changes

Top: incoming and outgoing summary — commit counts, and explicitly whether any
incoming commit touches a file the user currently holds a lock on.

Middle: a `QTableWidget` of changed files with a checkbox column for staging.
Columns: State (MOD/NEW/DEL), Path, Size, Lock.

Bottom: a commit message `QPlainTextEdit`, `Commit`, `Commit and push`.

### 9.6 Locked-by-someone-else

A modal raised whenever a lock attempt is refused. States who holds it and since
when, and offers exactly two choices: *Cancel* and *Open read-only*. There is no
steal option. A `Copy details` button puts the path, holder, and timestamp on the
clipboard so the artist can paste it into chat — which is the actual resolution
path, and it is a human one, correctly so at five people.

### 9.7 Conflict resolution — whole-file pick

Locking makes conflicts rare, not impossible: someone edits without locking,
edits from outside Kiln, or a lock gets released early. When it happens the
artist needs a way through that does not involve learning git.

**Normal pull stays `git pull --ff-only`.** It is only when that fails on
divergence that Kiln offers a merge (`git merge <remote-branch>`), and only then
that this screen appears.

A `QTableWidget` of conflicted files, one row each: path, your version's size and
modified time, the server version's size, author, and commit message. Two
buttons per row — **Keep my version** and **Keep server version** — plus a
per-row resolved indicator. A single `Complete merge` button, enabled once every
row is resolved, and an always-available `Abandon merge`.

Per-file resolution sequence:

1. **Back up both sides first**, to `.kiln/conflicts/<timestamp>/<path>/`, as
   `mine` and `theirs`. This happens before anything is overwritten.
2. `git checkout --ours -- <path>` or `git checkout --theirs -- <path>`.
3. `git lfs checkout -- <path>` — the checkout above can leave an LFS pointer
   rather than real content. Verify the result is not a pointer (7.3) before
   continuing. Handing a DCC a pointer file is the failure mode to avoid here.
4. `git add -- <path>` to mark it resolved.

Then `Complete merge` runs `git commit` with a generated message naming each file
and which side won. `Abandon merge` runs `git merge --abort` and is offered
prominently at every step.

Step 1 is not optional. "Keep server version" silently destroys an artist's work
otherwise, and this screen will be used by someone who is already stressed. The
backup is also what lets them undo a wrong click without understanding what
happened.

Scope limits, enforced:

- Only files where a whole-file pick is meaningful — LFS-tracked binaries.
- A **text file** conflict (`.gitattributes`, JSON configs, anything not
  LFS-tracked) is not offered a pick. It disables `Complete merge` and directs
  the artist to the CLI, because picking a whole side of a text file is usually
  wrong and occasionally destroys a co-worker's edit invisibly.
- Add/add, delete/modify, and rename conflicts are not handled. Same escalation.

### 9.8 Clone

A `QDialog`: repo URL, local destination with a folder picker, progress, cancel.
That is all. No folder selection (7.3).

## 10. Safety requirements

Hard requirements. A v1 that violates any of them does not ship.

### 10.1 Locking is deliberate, and never automatic on browse

A lock is taken only as part of an explicit `Open for editing` action (9.4).
Browsing, selecting, and double-clicking never lock anything.

**Kiln never auto-releases a lock.** Not on application close, not on a timer,
not on detecting a DCC exited. Locks are released explicitly, or on a successful
push of that file. Any scheme that guesses when an artist is "finished" is wrong,
and being wrong costs someone a day of work.

### 10.2 Lock state is never presented as fresh when it isn't

Locks live on the server. A stale "not locked" is the worst bug this application
can produce, because it causes two people to edit the same file.

- A background `git lfs locks` refresh runs every 60s and after every remote op.
- Every screen showing lock state also shows its age ("locks as of 12s ago").
- Older than 5 minutes, or last refresh failed: the lock column renders in a
  clearly degraded state and the status bar says lock state is stale.
- **Before taking a lock, Kiln re-queries the server**, regardless of cache age.
  Displayed state is a hint; the lock operation itself is authoritative.

### 10.3 Offline is a visible, restricted mode

No offline requirement, so offline is handled by refusing rather than queueing.
When the server is unreachable:

- A persistent banner says so.
- Lock, unlock, pull, and push are disabled.
- Browse, detail, history, and local commit remain available.
- Nothing is queued for later replay. Queued remote operations are a source of
  surprising behavior and there is no requirement justifying them.

### 10.4 Destructive operations are recoverable

`Discard changes` does not call `git checkout --` directly. It first copies the
current file to `.kiln/trash/<timestamp>/<path>` inside the working tree, then
discards. The trash is never auto-pruned in v1. `.kiln/` is gitignored. At 7 GB
the disk cost of never pruning is irrelevant.

Every destructive action is confirmed with a dialog naming the exact files and
their total size.

### 10.5 Unrecognized repo state disables writing

Kiln understands exactly one repository shape: on the shared branch, no merge or
rebase in progress, no conflicts, not detached.

Anything else — a conflicted pull, a detached HEAD, the aftermath of someone
force-pushing, a mid-rebase state — sets `clean_state = False`, which:

- shows a banner naming the condition in plain language,
- disables commit, push, pull, lock, and discard,
- offers **Open terminal here** and a link to the escalation doc.

This single rule is Kiln's entire answer to advanced git. It does not attempt
recovery, and it will not let an artist make the situation worse by clicking
things. Q6 confirmed: this is acceptable and intended.

### 10.6 Kiln is never required

The repository stays an entirely ordinary git+LFS repository. Kiln writes no
custom metadata into it and performs no operation a person could not perform at a
terminal. Pin config lives outside the repo.

Consequence: "Kiln is broken" degrades to "use the command line today," not "the
team is stopped." With a single maintainer, that is the difference between an
inconvenience and an outage.

## 11. Performance

At 500 files and 7 GB, with 2–3 concurrent users, there are no performance
requirements. `git status` completes in milliseconds. Full-snapshot refresh on
every change is correct and cheap.

The only rules:

- Never block the UI thread on a subprocess, regardless of how fast it is.
- Debounce refreshes; don't re-queue one already in flight.

If the repo grows past roughly 10k files, revisit `core.fsmonitor` and
`core.untrackedCache`. Not before.

## 12. Errors and escape hatches

- Every git invocation is logged with its full argv, exit code, duration, and
  stderr, to a rotating log file in the platform log dir.
- Every error dialog has a **Copy diagnostics** button copying the failing
  command, its output, and the git/LFS versions.
- The window has an **Open terminal here** action in the working tree directory.
- An advanced panel shows the last 20 commands run, verbatim.

These cost almost nothing and convert unreproducible artist bug reports into a
pasteable transcript. With one maintainer, this is not a nicety. It is also the
mechanism by which "delegate to the CLI" (10.5) is actually usable rather than
just a policy.

## 13. Testing

- `kiln/git` and `kiln/core` are tested with pytest against **real temporary
  repositories** built by fixtures: `git init`, commit files, install LFS. No
  mocking of git itself; a mocked subprocess interface tests nothing real.
- Lock paths are tested against a local Gitea in docker. Given that locking is
  the whole point of the product, this is worth the setup cost once.
- Parser tests are pinned against captured real output for every command in 6.1,
  so a git version bump that changes output fails a test rather than a user.
- The read-only/writable permission behavior (7.1) is tested on both Windows and
  Linux, since it is the one place OS differences are expected.
- The UI layer gets no automated tests in v1. It is thin by construction.

## 14. Packaging and distribution

- PyInstaller, one-folder mode — not one-file, whose temp extraction makes
  antivirus behavior and startup time worse on Windows.
- Kiln does **not** bundle `git` or `git-lfs`. It detects them on PATH and, if
  missing or too old, shows an install instruction screen. Bundling git is a
  maintenance liability and a security update obligation you do not want.
- Version string visible in the window title and in diagnostics.
- Distribution is a zip on the internal server. No auto-update in v1; updating is
  "download the new zip." Auto-update is a whole subsystem and a whole class of
  failure, for five users who sit near each other.

## 15. Milestones

| # | Deliverable | Rough |
|---|---|---|
| M0 | `kiln/git` + `kiln/core` with pytest fixtures. No UI. Status, log, locks, ahead/behind, pointer detection all correct headless. | 1 wk |
| M1 | Read-only app: window, sidebar tree, browse table, file detail, history. Pins with depth, persisted. Ship this to one artist. | 1.5 wk |
| M2 | Locking: list, take, release, staleness, the Open sequence (9.4), refused-lock modal, writable-bit handling. Gitea integration confirmed. | 1.5 wk |
| M3 | Write path: stage, commit, push, pull, changes screen, discard with trash, clean-state guard. | 2 wk |
| M4 | Hardening: offline mode, diagnostics, logging, error paths, packaging, Linux build, one week of real-team dogfooding. | 2 wk |

Roughly 8 weeks focused full-time. Assume more; M4 always grows, and the first
week of real artist use always finds something structural.

**Ship M1 before building M2.** A read-only browser cannot damage anything, and
putting it in front of one artist will tell you more about whether pinned folders
and depth are the right idea than any further specification will.

## 16. Deferred to v2+

Ordered by how often it will get asked for:

1. The Blender addon. It calls `git lfs lock` directly (7.5); nothing in
   Kiln has to ship first.
2. Real thumbnails / preview pane.
3. Lock request / notification flow ("ask Devin for stone_arch_A").
4. "Open with" application selection.
5. Selective clone / `lfs.fetchinclude` — when the repo passes ~50 GB.
6. macOS build.
7. Auto-update.

## 17. Remaining open questions

All six original questions are answered. What's left is smaller, and none of it
blocks M0 or M1:

- **Q7** — What is the shared branch called, and is it protected server-side
  against force-push? Kiln's clean-state guard (10.5) handles the aftermath, but
  a Gitea branch protection rule prevents it entirely and costs one checkbox.
- **Q8** — When a lock is released by push (10.1), is that *all* files in the
  push, or only files the artist explicitly finished with? Recommendation: all
  files in the push, since pushing is the artist's signal that the work is shared.
  Confirm before M3.
- **Q9** — Should `Open for editing` be blocked when the file is behind the
  remote (someone pushed a newer version)? Recommendation: warn and offer to pull
  first, but allow. Confirm before M2.
