@echo off

setlocal
cd /d "%~dp0"

set "EXITCODE=0"
set "PYTHON=.venv\Scripts\python.exe"

REM --- virtual environment ---------------------------------------------
if not exist "%PYTHON%" (
    echo No virtual environment found. Creating .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment.
        echo Is Python 3.11 or newer installed and on PATH?
        goto :failed
    )
)

REM --- dependencies ----------------------------------------------------
REM Checked every run rather than only on first setup, so an existing
REM .venv created before a dependency was added still builds.
"%PYTHON%" -c "import PySide6, PyInstaller, zstandard" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ...
    "%PYTHON%" -m pip install --upgrade pip
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install dependencies.
        goto :failed
    )
)

REM --- build -----------------------------------------------------------
echo.
"%PYTHON%" scripts\build_release.py
if errorlevel 1 goto :failed

echo.
echo Done. The artifacts above are in the dist folder.
goto :finish

:failed
echo.
echo ******************  BUILD FAILED  ******************
echo Scroll up for the error.
set "EXITCODE=1"

:finish
REM Pause only when double-clicked, so running this from a terminal or a
REM script does not hang waiting for a keypress.
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 pause

exit /b %EXITCODE%
