@echo off
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
echo Building mkvsyncdub.exe...
if exist dist\mkvsyncdub.exe (
    del /f /q dist\mkvsyncdub.exe
    if exist dist\mkvsyncdub.exe (
        echo.
        echo Build failed - could not replace dist\mkvsyncdub.exe.
        echo Close any running copy of mkvsyncdub.exe and try again.
        exit /b 1
    )
)

python -m PyInstaller mkvsyncdub.spec --clean
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
