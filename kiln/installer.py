"""Replacing the running Kiln with a downloaded build.

A running program cannot overwrite itself on Windows, so the swap is done by a
small batch script started just before Kiln quits. It waits for Kiln to exit,
moves the new build into place, starts it, and deletes itself. Everything that
can fail in a way worth explaining (the download, the zip, whether the folder
is writable at all) happens here, beforehand, while Kiln can still say so.

The two kinds of build are replaced differently:

- One-folder (from the zip): the whole folder is swapped. The old folder is
  moved aside rather than deleted first, and moved back if the new one cannot
  be put in its place.
- Portable (single exe): the exe is replaced in place, keeping whatever name
  the artist gave it.

The new build is staged beside the installation rather than in %TEMP%, so
putting it in place is a rename on the same drive, never a copy that can stop
halfway. If the script cannot finish, it starts whichever version is in place,
so the artist always gets Kiln back; update.log in the config directory says
what happened.

Only CI builds of Kiln on Windows update themselves. No Qt here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kiln.core.settings import config_directory

FOLDER = "folder"
PORTABLE = "portable"

EXECUTABLE_NAME = "Kiln.exe"

# Environment variables PyInstaller's bootloader uses to hand state to its own
# child process. Left in place, the new Kiln would take itself for part of the
# old one and look for files in a temporary folder that no longer exists.
PYINSTALLER_VARIABLE_PREFIXES = ("_PYI_", "_MEIPASS")

# Paths reach the script through environment variables, never through its
# text: cmd parses its own script, and a folder called "R&D" or "100%" would
# otherwise be parsed with it.
#
# Log messages never include a path: :log echoes its argument unquoted.
#
# Every move is retried, because antivirus and the search indexer hold freshly
# written files open for a few seconds. Whatever happens, the script starts the
# Kiln that ends up in place (LAUNCH), unless Kiln never exited, in which case
# it is still running and nothing is touched.
HELPER_SCRIPT = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "LAUNCH=%KILN_RELAUNCH%"
call :log "waiting for Kiln (process %KILN_WAIT_PID%) to exit"

set /a TRIES=0
:wait
tasklist /FI "PID eq %KILN_WAIT_PID%" /NH 2>nul | find "%KILN_WAIT_PID%" >nul
if errorlevel 1 goto exited
set /a TRIES+=1
if %TRIES% geq 120 (call :log "Kiln did not exit; not updating" & goto abandon)
call :pause
goto wait

:exited
if "%KILN_KIND%"=="portable" goto portable

if exist "%KILN_BACKUP%" rmdir /s /q "%KILN_BACKUP%"
if exist "%KILN_BACKUP%" (call :log "an earlier backup could not be removed; not updating" & goto finish)

set /a TRIES=0
:move_old
move "%KILN_TARGET%" "%KILN_BACKUP%" >nul 2>&1
if not errorlevel 1 goto move_new
set /a TRIES+=1
if %TRIES% geq 30 (call :log "could not move the old version aside; it is still in place" & goto finish)
call :pause
goto move_old

:move_new
set /a TRIES=0
:move_new_again
move "%KILN_STAGED%" "%KILN_TARGET%" >nul 2>&1
if not errorlevel 1 goto swapped
set /a TRIES+=1
if %TRIES% geq 30 (call :log "could not move the new version into place; restoring the old one" & goto restore)
call :pause
goto move_new_again

:restore
set /a TRIES=0
:restore_again
move "%KILN_BACKUP%" "%KILN_TARGET%" >nul 2>&1
if not errorlevel 1 (call :log "the old version is back in place" & goto finish)
set /a TRIES+=1
if %TRIES% geq 30 goto stranded
call :pause
goto restore_again

:stranded
call :log "could not restore the old version; it is in the -previous folder beside it"
set "LAUNCH=%KILN_STRANDED%"
goto finish

:swapped
set "LAUNCH=%KILN_LAUNCH%"
rmdir /s /q "%KILN_BACKUP%" 2>nul
call :log "updated"
goto finish

:portable
set /a TRIES=0
:replace_exe
move /y "%KILN_STAGED%" "%KILN_TARGET%" >nul 2>&1
if not errorlevel 1 (call :log "updated" & goto finish)
set /a TRIES+=1
if %TRIES% geq 30 (call :log "could not replace the exe; the old version is still in place" & goto finish)
call :pause
goto replace_exe

:finish
rmdir /s /q "%KILN_STAGING%" 2>nul
call :log "starting Kiln"
start "" "%LAUNCH%"
(goto) 2>nul & del "%~f0"

:abandon
rmdir /s /q "%KILN_STAGING%" 2>nul
(goto) 2>nul & del "%~f0"

:pause
ping -n 2 127.0.0.1 >nul
exit /b 0

:log
>>"%KILN_LOG%" echo %DATE% %TIME% %~1
exit /b 0
"""

# How cmd is asked to run the script. The script's own path goes through the
# environment too, since %TEMP% can contain "&" just as well; with /s, cmd
# drops only the outermost quotes and keeps the path quoted after expansion.
HELPER_COMMAND = 'cmd.exe /d /s /c ""%KILN_SCRIPT%""'


class InstallError(Exception):
    """The update cannot be put in place. The message is fit to show an artist."""


@dataclass(frozen=True)
class Installation:
    kind: str  # FOLDER or PORTABLE
    executable: Path

    @property
    def root(self) -> Path:
        """What gets replaced: the build folder, or the portable exe itself."""
        return self.executable.parent if self.kind == FOLDER else self.executable

    @property
    def artifact_kind(self) -> str:
        """Which file of a release this installation is updated from."""
        return "zip" if self.kind == FOLDER else "portable"

    @property
    def staging_directory(self) -> Path:
        return self.root.parent / f".{self.root.name}-update"

    @property
    def backup_directory(self) -> Path:
        return self.root.parent / f".{self.root.name}-previous"

    def download_path(self, file_name: str) -> Path:
        return self.staging_directory / file_name


def detect(executable: Path, bundle_directory: Path) -> Installation:
    """Tell a one-folder build from a portable one.

    A one-folder build unpacks into _internal beside its exe (or, built with
    older PyInstaller, into the exe's own folder); a portable one unpacks into
    a fresh temporary folder on every start. Only those two exact places
    count: a portable exe kept somewhere above %TEMP%, such as the user's home
    folder, must not pass for a one-folder build of that whole folder.
    """
    executable = executable.resolve()
    bundle_directory = bundle_directory.resolve()
    home = executable.parent
    if bundle_directory in (home, home / "_internal"):
        return Installation(FOLDER, executable)
    return Installation(PORTABLE, executable)


def current_installation() -> Installation | None:
    """The running installation, if it is one Kiln knows how to replace."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    bundle_directory = getattr(sys, "_MEIPASS", None)
    if bundle_directory is None:
        return None
    return detect(Path(sys.executable), Path(bundle_directory))


def unavailable_reason(installation: Installation | None) -> str:
    """Why this installation cannot update itself, or "" if it can."""
    if installation is None:
        return "Only the Windows builds of Kiln can update themselves."
    try:
        with tempfile.TemporaryFile(dir=installation.root.parent):
            pass
    except OSError:
        return (
            f"Kiln cannot write to {installation.root.parent}, so it cannot "
            "replace itself there. Download the new version by hand, or move "
            "Kiln to a folder you can write to."
        )
    return ""


def leave_installation_folder() -> None:
    """Stop holding the installation folder open as the working directory.

    Started from Explorer or a shortcut, Kiln's working directory is its own
    folder, and every program it opens (Blender, the file browser) inherits
    it. Windows will not rename a folder that is any running program's working
    directory, so while one of them stayed open the one-folder update could
    never move the old build aside.
    """
    if not getattr(sys, "frozen", False):
        return
    home = Path(sys.executable).resolve().parent
    try:
        working = Path.cwd().resolve()
    except OSError:
        return
    if working == home or home in working.parents:
        try:
            os.chdir(Path.home())
        except OSError:
            pass


def clear_staging(installation: Installation) -> None:
    """Remove what an earlier, abandoned update left behind."""
    _remove_tree(installation.staging_directory)


def prepare(installation: Installation, downloaded: Path) -> Path:
    """Turn a verified download into the build that will replace this one."""
    if installation.kind == PORTABLE:
        return downloaded

    extracted = installation.staging_directory / "extracted"
    _remove_tree(extracted)
    try:
        with zipfile.ZipFile(downloaded) as archive:
            _check_members(archive)
            archive.extractall(extracted)
    except (OSError, zipfile.BadZipFile) as error:
        raise InstallError(f"The downloaded build could not be unpacked ({error}).") from error

    candidates = [path.parent for path in extracted.glob(f"*/{EXECUTABLE_NAME}")]
    if len(candidates) != 1:
        raise InstallError("The downloaded build does not contain Kiln.")
    return candidates[0]


def helper_environment(
    installation: Installation, staged: Path, wait_pid: int, base: dict[str, str]
) -> dict[str, str]:
    """What the helper script is told, on top of the environment it inherits."""
    environment = {
        key: value
        for key, value in base.items()
        if not key.upper().startswith(PYINSTALLER_VARIABLE_PREFIXES)
    }
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    if installation.kind == FOLDER:
        # The new build's launcher is always Kiln.exe; the old one keeps
        # whatever name it had, in place or stranded in the backup.
        launch = installation.root / EXECUTABLE_NAME
        relaunch = installation.executable
        stranded = installation.backup_directory / installation.executable.name
    else:
        launch = relaunch = stranded = installation.root

    environment.update(
        KILN_KIND=installation.kind,
        KILN_WAIT_PID=str(wait_pid),
        KILN_TARGET=str(installation.root),
        KILN_STAGED=str(staged),
        KILN_BACKUP=str(installation.backup_directory),
        KILN_STAGING=str(installation.staging_directory),
        KILN_LAUNCH=str(launch),
        KILN_RELAUNCH=str(relaunch),
        KILN_STRANDED=str(stranded),
        KILN_LOG=str(config_directory() / "update.log"),
    )
    return environment


def launch_replacement(installation: Installation, staged: Path) -> None:
    """Start the script that swaps `staged` in once Kiln has exited.

    The caller must quit Kiln straight after; the script waits for that.
    """
    # A portable build runs as two processes: the bootloader, which holds the
    # exe open and cleans up after Python, and Python itself. Only once the
    # bootloader has gone can the exe be replaced.
    wait_pid = os.getppid() if installation.kind == PORTABLE else os.getpid()

    script = Path(tempfile.gettempdir()) / f"kiln-update-{os.getpid()}.cmd"
    environment = helper_environment(installation, staged, wait_pid, dict(os.environ))
    environment["KILN_SCRIPT"] = str(script)
    try:
        script.write_text(HELPER_SCRIPT.replace("\n", "\r\n"), encoding="ascii", newline="")
        subprocess.Popen(
            HELPER_COMMAND,  # a string, so Python adds no quoting of its own
            cwd=tempfile.gettempdir(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except OSError as error:
        raise InstallError(f"Could not start the updater ({error}).") from error


def _check_members(archive: zipfile.ZipFile) -> None:
    """Refuse a zip that would write outside the folder it is unpacked into."""
    for name in archive.namelist():
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in name:
            raise InstallError("The downloaded build contains a file Kiln will not unpack.")


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
