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

analysis = Analysis(
    ["run_kiln.py"],
    pathex=["."],
    binaries=[],
    datas=[("kiln/assets/icons", "kiln/assets/icons")],
    hiddenimports=collect_submodules("kiln"),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT_MODULES + EXCLUDED_MODULES,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Kiln",
    debug=False,
    strip=False,
    upx=False,  # UPX compression is a reliable way to get flagged by antivirus
    # Windowed: no console window behind the app. Everything that would have
    # gone to a console is in the log file and the Diagnostics panel instead.
    # Flip this to True while debugging a packaged build.
    console=False,
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Kiln",
)
