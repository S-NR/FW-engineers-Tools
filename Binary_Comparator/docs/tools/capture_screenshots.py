"""Capture documentation screenshots of the GUI (Windows; needs Pillow for ImageGrab).

    python docs/tools/capture_screenshots.py

Writes PNG files to docs/images/. The application window is kept on top while
capturing so no other window content ends up in the images. User settings are
redirected to a temporary folder.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "docs", "images")
TB = os.path.join(ROOT, "Test Binaries")
EX = os.path.join(ROOT, "examples")
os.environ["FWCMP_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="fwcmp_doc_settings_")
sys.path.insert(0, ROOT)

from PIL import ImageGrab  # noqa: E402

from firmware_binary_tool.gui.main_window import MainWindow, create_root  # noqa: E402


def settle(root, n=40):
    for _ in range(n):
        root.update()
        time.sleep(0.03)


def grab(win, name):
    win.attributes("-topmost", True)
    win.lift()
    win.focus_force()
    settle(win, 45)
    x, y = win.winfo_rootx(), win.winfo_rooty()
    img = ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height()))
    if img.convert("L").getextrema()[1] < 50:  # black frame: compositor not ready, retry once
        settle(win, 60)
        img = ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height()))
    img.save(os.path.join(OUT, name))
    print("saved", name)


def wait_session(root, w, timeout=90):
    t0 = time.time()
    while (w.session is None or w.runner.busy or w._queue) and time.time() - t0 < timeout:
        root.update()
        time.sleep(0.02)
    settle(root, 20)


def new_window():
    root, dnd = create_root()
    w = MainWindow(root, dnd)
    root.geometry("1500x960+10+10")
    settle(root, 20)
    return root, w


def main():
    os.makedirs(OUT, exist_ok=True)

    # ---------------------------------------------------------------- 1. ARM watchdog change (real firmware)
    root, w = new_window()
    w.nb.select(w.files_tab)
    grab(root, "01_files_empty.png")
    ab, abw = os.path.join(TB, "AB FW"), os.path.join(TB, "AB FW - Watchdog Commented")
    w.load_symbols("A", os.path.join(ab, "Analysis_Board_Firmware.elf"))
    w.load_symbols("B", os.path.join(abw, "Analysis_Board_Firmware.elf"))
    w.load_file(w.panel_a, os.path.join(ab, "Analysis_Board_Firmware.hex"))
    w._queue.append(lambda: w.load_file(w.panel_b, os.path.join(abw, "Analysis_Board_Firmware.hex")))
    wait_session(root, w)
    grab(root, "03_summary_watchdog.png")
    w.nb.select(w.files_tab)
    grab(root, "02_files_loaded.png")
    w.nb.select(w.features_view)
    grab(root, "04_changed_functions.png")
    w.features_view.show_box.set("Relocated / moved only")
    w.features_view.refresh_symbols()
    grab(root, "04b_relocated.png")
    w.features_view.show_box.set("Real changes")
    w.features_view.refresh_symbols()
    target = next(c for c in w.session.features.symbols if c.name == "peripheralInit")
    w.open_hex(target.addr_b)
    hw = w.hex_window
    hw.geometry("1400x760+40+40")
    settle(root, 30)
    hw.set_cursor(w.session.diff.diff.first_at_or_after(target.addr_b), center=True)
    grab(hw, "05_hex_viewer.png")
    hw.row_box.set("1 byte/row (table)")
    hw.rebuild_model()
    grab(hw, "05b_hex_table.png")
    hw.destroy()
    w.on_close()

    # ---------------------------------------------------------------- 2. STM32 example project (regions + expected)
    root, w = new_window()
    w.config_editor.load(os.path.join(EX, "project_stm32.json"))
    w.load_file(w.panel_a, os.path.join(EX, "firmware", "FW_V1.2.3.elf"))
    w._queue.append(lambda: w.load_file(w.panel_b, os.path.join(EX, "firmware", "FW_V1.2.4.elf")))
    wait_session(root, w)
    grab(root, "06_summary_expected.png")
    for name, tab in (("07_regions.png", w.region_table), ("08_changed_blocks.png", w.block_table),
                      ("09_memory_map.png", w.memory_map), ("10_configuration.png", w.config_editor),
                      ("11_metadata.png", w.metadata_view), ("13_elf.png", w.elf_view)):
        w.nb.select(tab)
        grab(root, name)
    w.nb.select(w.checksum_view)
    w.checksum_view.alg.set("CRC-32")
    w.checksum_view.scope.set("Region: Calibration")
    w.checksum_view._scope_changed()
    w.checksum_view.calculate()
    t0 = time.time()
    while w.checksum_view.runner.busy and time.time() - t0 < 30:
        root.update()
        time.sleep(0.02)
    grab(root, "12_checksums.png")
    w.on_close()

    # ---------------------------------------------------------------- 3. PIC32 controller identification
    root, w = new_window()
    w.load_symbols("A", os.path.join(TB, "production_old", "SYSCTRL_NEW.X.production.map"))
    w.load_symbols("B", os.path.join(TB, "production_new", "SYSCTRL_NEW.X.production.map"))
    w.load_file(w.panel_a, os.path.join(TB, "production_old", "SYSCTRL_NEW.X.production.hex"))
    w._queue.append(lambda: w.load_file(w.panel_b, os.path.join(TB, "production_new", "SYSCTRL_NEW.X.production.hex")))
    wait_session(root, w)
    w.nb.select(w.files_tab)
    grab(root, "14_controller_pic32.png")
    w.nb.select(w.summary_tab)
    grab(root, "15_summary_pic32.png")
    w.on_close()


if __name__ == "__main__":
    main()
