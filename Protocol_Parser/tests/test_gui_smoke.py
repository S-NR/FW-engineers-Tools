"""GUI smoke tests (run headless with the Qt 'offscreen' platform)."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests  # noqa: F401,E402

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui import theme
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_theme(cls.app, "dark")

    def setUp(self):
        from gui.main_window import MainWindow
        from gui.settings import Settings
        from protocols.protocol_manager import ProtocolManager
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        manager = ProtocolManager()
        manager.reload()
        self.window = MainWindow(self.settings, manager)
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.tmp.cleanup()

    def table_rows(self):
        t = self.window.decoded.table
        return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]

    def test_first_launch_state(self):
        self.assertEqual(self.window.protocol_combo.currentText(), "Dextens USB Protocol")
        self.assertEqual(self.window.input.text(), "02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03")

    def test_decode_button_shows_fields(self):
        QTest.mouseClick(self.window.input.decode_btn, Qt.MouseButton.LeftButton)
        rows = self.table_rows()
        self.assertEqual([r[2] for r in rows], ["STX", "Address", "Length", "Payload", "CRC", "ETX"])
        self.assertEqual(rows[3][4], '"get diag"')
        self.assertTrue(rows[4][5].startswith("VALID"))
        self.assertIn("Packet valid", self.window.status_indicator.text())
        self.assertEqual(len(self.window.history), 1)

    def test_ctrl_enter_decodes(self):
        self.window.input.editor.setFocus()
        QTest.keyClick(self.window.input.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.window.decoded.table.rowCount(), 6)
        self.assertNotIn("\n", self.window.input.text())

    def test_invalid_input_does_not_crash(self):
        for text in ("02 0G", "021", "", "02 01"):
            self.window.input.set_text(text)
            self.window.decode_current()
        self.assertIn("✗", self.window.status_indicator.text())

    def test_bitfield_rows_and_selection(self):
        self.window.protocol_combo.setCurrentText("Simple Sensor Packet")
        self.window.input.set_text("AA 55 01 02 00 EB 01 C2 A5 55")
        self.window.decode_current()
        names = [r[2].strip() for r in self.table_rows()]
        self.assertIn("└ Fault", names)
        self.window.decoded.table.selectRow(names.index("└ Fault"))  # selecting a bit row must not fail

    def test_tabs_and_tools(self):
        w = self.window
        w.analyze_current()
        self.assertIn("Possible interpretation", w.decoded.analysis_view.toPlainText())
        w.txrx.analyze()
        self.assertIn("diag:OK", w.txrx.summary.toPlainText())
        w.diff.analyze()
        self.assertIn("Incrementing", w.diff.findings.toPlainText())
        w.set_theme("light")
        w.set_theme("dark")

    def test_dialogs_construct(self):
        from gui.crc_calculator import CRCCalculatorDialog
        from gui.packet_builder import PacketBuilderDialog
        from gui.protocol_editor import FieldEditDialog, ProtocolEditorDialog
        proto = self.window.current_protocol()
        editor = ProtocolEditorDialog(proto, self.window.manager)
        self.assertEqual(editor.table.rowCount(), 6)
        crc_field = proto.field_by_name("CRC")
        fed = FieldEditDialog(crc_field, ["STX", "Address", "Length", "Payload"])
        rebuilt = fed._build()
        self.assertEqual(rebuilt.to_dict(), crc_field.to_dict())
        payload = proto.field_by_name("Payload")
        self.assertEqual(FieldEditDialog(payload, ["STX", "Address", "Length"])._build().to_dict(), payload.to_dict())
        builder = PacketBuilderDialog(proto, {"Address": "1", "Payload": "get diag"})
        self.assertEqual(builder.output.text(), "02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03")
        calc = CRCCalculatorDialog("01 08 00 67 65 74 20 64 69 61 67")
        values = {calc.table.item(r, 0).text(): calc.table.item(r, 3).text() for r in range(calc.table.rowCount())}
        self.assertEqual(values["CRC-16/MODBUS"], "DD 1F")

    def test_ingest_packet_hook(self):
        from transport import LoopbackTransport
        t = LoopbackTransport()
        results = []
        t.set_rx_callback(lambda data: results.append(self.window.ingest_packet(data, "RX")))
        t.open()
        t.write(bytes.fromhex("020108006765742064696167DD1F03"))
        self.assertTrue(results[0].ok)
        self.assertEqual(self.window.history[-1].direction, "RX")


if __name__ == "__main__":
    unittest.main()
