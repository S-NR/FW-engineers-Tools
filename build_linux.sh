#!/usr/bin/env bash
# Build standalone Linux executables with PyInstaller.
# Prerequisite (Debian/Ubuntu): sudo apt install python3-tk python3-venv
# Output: dist/FirmwareCompare/FirmwareCompare (GUI) and dist/firmware-diff/firmware-diff (CLI)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .

python -m PyInstaller --noconfirm --clean --windowed --name FirmwareCompare \
    --collect-all tkinterdnd2 \
    --add-data "firmware_binary_tool/config/schema.json:firmware_binary_tool/config" \
    firmware_compare_gui.py

python -m PyInstaller --noconfirm --clean --console --name firmware-diff firmware_diff.py

echo
echo "Build complete:"
echo "  dist/FirmwareCompare/FirmwareCompare"
echo "  dist/firmware-diff/firmware-diff"
