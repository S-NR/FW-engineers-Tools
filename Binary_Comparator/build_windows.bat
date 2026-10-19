@echo off
REM Build standalone Windows executables with PyInstaller.
REM Output: dist\FirmwareCompare\FirmwareCompare.exe (GUI) and dist\firmware-diff\firmware-diff.exe (CLI)
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-dev.txt || goto :error
python -m unittest discover -s tests -t . || goto :error

python -m PyInstaller --noconfirm --clean --windowed --name FirmwareCompare ^
    --collect-all tkinterdnd2 ^
    --add-data "firmware_binary_tool\config\schema.json;firmware_binary_tool\config" ^
    firmware_compare_gui.py || goto :error

python -m PyInstaller --noconfirm --clean --console --name firmware-diff firmware_diff.py || goto :error

echo.
echo Build complete:
echo   dist\FirmwareCompare\FirmwareCompare.exe
echo   dist\firmware-diff\firmware-diff.exe
exit /b 0

:error
echo Build FAILED.
exit /b 1
