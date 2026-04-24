@echo off
setlocal

set "BUILD_ARGS="
if /I "%~1"=="clean" (
    set "BUILD_ARGS=--clean"
)

for /f %%I in ('powershell -NoProfile -Command "(Get-Process -Name 'mkvsyncdub' -ErrorAction SilentlyContinue | Measure-Object).Count"') do set "RUNNING_COUNT=%%I"
if not "%RUNNING_COUNT%"=="0" (
    echo.
    echo Build failed - mkvsyncdub.exe is still running.
    echo Close the app and try again.
    exit /b 1
)

echo Checking PyInstaller...
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found; installing...
    python -m pip install pyinstaller pyinstaller-hooks-contrib
    if errorlevel 1 (
        echo.
        echo Failed to install PyInstaller.
        exit /b 1
    )
) else (
    echo PyInstaller is already installed.
)

python -c "import PyInstaller; import PyInstaller.__main__" >nul 2>nul
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

if "%BUILD_ARGS%"=="--clean" (
    echo Running a clean PyInstaller build...
) else (
    echo Running an incremental PyInstaller build. Use "build clean" for a full clean rebuild.
)

python -m PyInstaller mkvsyncdub.spec %BUILD_ARGS%
if errorlevel 1 (
    echo.
    echo Build failed - PyInstaller exited with an error.
    exit /b 1
)

echo.
if exist dist\mkvsyncdub.exe (
    echo Build successful: dist\mkvsyncdub.exe
) else (
    echo Build failed - check output above.
    exit /b 1
)
