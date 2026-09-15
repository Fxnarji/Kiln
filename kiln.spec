# PyInstaller build description for Kiln.
#
#     .venv/Scripts/pyinstaller kiln.spec        # Linux: .venv/bin/pyinstaller
#
# Output lands in dist/Kiln/. Ship that whole folder — one-folder mode is
# deliberate. One-file mode unpacks to a temp directory on every launch, which
# is slower and is what antivirus software on Windows tends to object to.
#
# Kiln does NOT bundle git or git-lfs. It finds them on PATH and refuses to
# start without them. Bundling them would mean owning their security updates.

import os

from PyInstaller.utils.hooks import collect_submodules

# Qt modules PySide6 offers that Kiln has no use for. Excluding them keeps the
# build to a sane size; QtWebEngine alone is well over a hundred megabytes.
EXCLUDED_QT_MODULES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

# Things that ride along with a default Python install and are pure weight here.
EXCLUDED_MODULES = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "test",
    "distutils",
    "setuptools",
    "pip",
    "numpy",
    "PIL",
]

# zstandard is a real runtime dependency: without it every preview falls back
# to a placeholder tile, because current Blender compresses .blend files.
REQUIRED_PACKAGES = ["zstandard"]

# The build stamp scripts/build_release.py writes (see kiln/build_info.py).
# A bare `pyinstaller kiln.spec` has none, and the result reports itself as a
# development build.
BUILD_INFO = (
    [("kiln/build_info.json", "kiln")] if os.path.isfile("kiln/build_info.json") else []
)

analysis = Analysis(
    ["run_kiln.py"],
    pathex=["."],
    binaries=[],
    datas=[("kiln/assets/icons", "kiln/assets/icons")] + BUILD_INFO,
    hiddenimports=collect_submodules("kiln") + REQUIRED_PACKAGES,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT_MODULES + EXCLUDED_MODULES,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

# Both targets share this one Analysis, so building them together costs far
# less than twice a single build.
#
# Common settings:
#   upx=False    UPX compression is a reliable way to get flagged by antivirus
#   console=False  no console window behind the app. Anything that would have
#                  gone to a console is in the log file and the Diagnostics
#                  panel instead. Flip to True while debugging a packaged build.

# --- Target 1: one folder -----------------------------------------------
# dist/Kiln/Kiln.exe plus dist/Kiln/_internal/
#
# Starts fastest and is the friendliest to antivirus, but the exe is only a
# launcher: it cannot run without _internal beside it. Ship the folder zipped,
# never the loose exe. scripts/build_release.py exists to enforce that.

folder_executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Kiln",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

COLLECT(
    folder_executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Kiln",
)

# --- Target 2: one file -------------------------------------------------
# dist/Kiln-portable.exe
#
# Everything in a single file, so it cannot be distributed incorrectly. The
# cost is paid at every launch: the bundle unpacks to a temporary directory
# first, which is slower to start and is the shape antivirus heuristics tend to
# dislike. For an application opened once and left running for a work session,
# that trade is usually worth it.

EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="Kiln-portable",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
