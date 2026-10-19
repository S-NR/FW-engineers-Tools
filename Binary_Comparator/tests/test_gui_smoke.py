"""GUI smoke test: drives the real Tk application without user interaction.

Skipped automatically when no display is available (e.g. headless CI) or when
FWCMP_SKIP_GUI_TESTS is set.
"""

import os
import time
import unittest

from .helpers import TempDirTestCase

try:
    import tkinter as tk
    HAVE_DISPLAY = bool(os.environ.get("DISPLAY") or os.name == "nt" or os.environ.get("WAYLAND_DISPLAY"))
except ImportError:
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY and not os.environ.get("FWCMP_SKIP_GUI_TESTS"), "no display available")
class GuiSmokeTest(TempDirTestCase):
    def test_load_compare_and_hex_viewer(self):
        os.environ["FWCMP_SETTINGS_DIR"] = self.tmp  # never touch the user's real settings
        from firmware_binary_tool.gui import settings as gui_settings
        gui_settings._instance = None
        from firmware_binary_tool.core.region_manager import MemoryRegion, ProjectConfig
        from firmware_binary_tool.gui.hex_model import FILTERS
        from firmware_binary_tool.gui.main_window import MainWindow

        old = bytearray(b"\xFF" * 0x4000)
        new = bytearray(old)
        new[0x2A41] = 0x27
        new[0x3000:0x3100] = b"\x00" * 0x100
        pa, pb = self.write("a.bin", bytes(old)), self.write("b.bin", bytes(new))
        cfg = self.path("cfg.json")
        ProjectConfig(base_address=0x08010000, regions=[MemoryRegion("App", 0x08010000, 0x08013FFF)]).save(cfg)

        errors = []
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *a: errors.append(a)
        win = MainWindow(root)
        try:
            win.config_editor.load(cfg)
            win.load_file(win.panel_a, pa)
            win._queue.append(lambda: win.load_file(win.panel_b, pb))
            deadline = time.time() + 30
            while win.session is None and time.time() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertIsNotNone(win.session)
            self.assertEqual(win.session.diff.first_diff, 0x08012A41)
            self.assertEqual(len(win.session.analysis.blocks), 2)
            for i in range(len(win.nb.tabs())):
                win.nb.select(i)
                root.update()
            win.open_hex()
            hw = win.hex_window
            hw.withdraw()
            hw.first_diff()
            self.assertEqual(hw.cursor, 0x08012A41)
            hw.next_diff()
            self.assertEqual(hw.cursor, 0x08013000)
            hw.prev_diff()
            self.assertEqual(hw.cursor, 0x08012A41)
            for flt in FILTERS:
                hw.filter_box.set(flt)
                hw.rebuild_model()
                root.update()
            hw.filter_box.set(FILTERS[0])
            hw.row_box.set("1 byte/row (table)")
            hw.rebuild_model()
            hw.goto_var.set("0x08012A40")
            hw.goto()
            self.assertEqual(hw.cursor, 0x08012A40)
            hw.search_var.set("27")
            hw.search(False)
            self.assertEqual(hw.cursor, 0x08012A41)
            root.update()
            self.assertEqual(errors, [])
        finally:
            win.on_close()


if __name__ == "__main__":
    unittest.main()
