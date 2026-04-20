@echo off
echo Installing/upgrading PyInstaller...
pip install --upgrade pyinstaller pyinstaller-hooks-contrib

echo.
echo Building mkvsyncdub.exe...
python -m PyInstaller mkvsyncdub.spec --clean

echo.
if exist dist\mkvsyncdub.exe (
    echo Build successful: dist\mkvsyncdub.exe
) else (
    echo Build failed - check output above.
    exit /b 1
)
