@echo off
setlocal

rem Clean by default so cached Qt DLLs from another Python/PySide environment
rem cannot leak into the executable. Use "build fast" only when dependencies
rem have not changed and a quick source-only rebuild is desired.
set "BUILD_ARGS=--clean"
set "PYTHON_CMD=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_CMD=.venv\Scripts\python.exe"

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo.
    echo Build requires Python 3.11. Python 3.13 currently produces a broken frozen QtGui runtime.
    echo Create .venv with: py -3.11 -m venv .venv
    echo Then install: .venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)

if /I "%~1"=="fast" (
    set "BUILD_ARGS="
)

for /f %%I in ('powershell -NoProfile -Command "(Get-Process -Name 'mkvsyncdub' -ErrorAction SilentlyContinue | Measure-Object).Count"') do set "RUNNING_COUNT=%%I"
if not "%RUNNING_COUNT%"=="0" (
    echo.
    echo Build failed - mkvsyncdub.exe is still running.
    echo Close the app and try again.
    exit /b 1
)

echo Checking PyInstaller...
%PYTHON_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found; installing...
    %PYTHON_CMD% -m pip install pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.7
    if errorlevel 1 (
        echo.
        echo Failed to install PyInstaller.
        exit /b 1
    )
) else (
    echo PyInstaller is already installed.
)

%PYTHON_CMD% -c "import PyInstaller; import PyInstaller.__main__" >nul 2>nul
if errorlevel 1 (
    echo.
    echo PyInstaller is installed but could not be imported.
    exit /b 1
)

echo.
if not exist assets\icons\app_icon.ico (
    echo Note: assets\icons\app_icon.ico not found; Windows builds will use the default exe icon.
    echo.
)

echo Building mkvsyncdub.exe...
if exist dist\mkvsyncdub.exe (
    del /f /q dist\mkvsyncdub.exe >nul 2>nul
    if exist dist\mkvsyncdub.exe (
        timeout /t 2 /nobreak >nul
        del /f /q dist\mkvsyncdub.exe >nul 2>nul
    )
    if exist dist\mkvsyncdub.exe (
        echo.
        echo Build failed - could not replace dist\mkvsyncdub.exe.
        echo Close any running copy of mkvsyncdub.exe, Explorer preview, or file handle and try again.
        exit /b 1
    )
)

if "%BUILD_ARGS%"=="" (
    echo Running an incremental PyInstaller build...
) else (
    echo Running a clean PyInstaller build. Use "build fast" for an incremental rebuild.
)

%PYTHON_CMD% -m PyInstaller mkvsyncdub.spec %BUILD_ARGS%
if errorlevel 1 (
    echo.
    echo Build failed - PyInstaller exited with an error.
    exit /b 1
)

echo.
if exist dist\mkvsyncdub.exe (
    echo Verifying packaged GUI startup...
    dist\mkvsyncdub.exe --gui-smoke-test >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Build failed - the packaged GUI could not start.
        exit /b 1
    )
    echo Build successful: dist\mkvsyncdub.exe
) else (
    echo Build failed - check output above.
    exit /b 1
)
