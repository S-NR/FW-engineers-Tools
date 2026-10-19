"""Encoder, comparison, diff analyzer, history, file extraction, AI interface, transport."""
import csv
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from ai import AIAnalyzer, HeuristicAnalyzer, get_analyzer
from ai.prompt import build_prompt
from core.comparison import byte_diff, compare_fields, text_fields
from core.decoder import decode_packet
from core.diff_analyzer import analyze_packets, classify_sequence
from core.encoder import EncodeError, encode_packet, user_fields
from core.history import PacketHistory
from tests.helpers import EXAMPLE_PACKET, dextens, load_example
from transport import LoopbackTransport, TransportError
from utils.file_utils import extract_packets, load_packets_from_file

RX_PACKET = bytes.fromhex("0201070064696167 3A4F4B DDBB 03".replace(" ", ""))


class EncoderTests(unittest.TestCase):
    def test_encode_reference_packet(self):
        self.assertEqual(encode_packet(dextens(), {"Address": 1, "Payload": "get diag"}), EXAMPLE_PACKET)

    def test_encode_decode_round_trip(self):
        sensor = load_example("Simple Sensor Packet")
        pkt = encode_packet(sensor, {"Device ID": "0x0102", "Temperature": "-12.3", "Humidity": 55.5,
                                     "Status": 0x21})
        r = decode_packet(sensor, pkt)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.field("Temperature").display, "-12.3 °C")

    def test_value_names_accepted(self):
        modbus = load_example("Modbus RTU")
        pkt = encode_packet(modbus, {"Slave Address": 1, "Function Code": "Read Holding Registers",
                                     "Data": "00 00 00 0A"})
        self.assertEqual(pkt, bytes.fromhex("01030000000AC5CD"))

    def test_user_fields_excludes_auto(self):
        self.assertEqual([f.name for f in user_fields(dextens())], ["Address", "Payload"])

    def test_errors(self):
        with self.assertRaises(EncodeError):
            encode_packet(dextens(), {"Payload": "x"})  # Address missing
        with self.assertRaises(EncodeError):
            encode_packet(dextens(), {"Address": 300, "Payload": "x"})
        with self.assertRaises(EncodeError):
            encode_packet(dextens(), {"Address": 1, "Payload": "°"})  # not ASCII


class ComparisonTests(unittest.TestCase):
    def test_tx_rx(self):
        proto = dextens()
        tx, rx = decode_packet(proto, EXAMPLE_PACKET), decode_packet(proto, RX_PACKET)
        self.assertTrue(rx.ok, rx.errors)
        self.assertEqual(text_fields(tx), [("Payload", "get diag")])
        self.assertEqual(text_fields(rx), [("Payload", "diag:OK")])
        diffs = {c.name: c.same for c in compare_fields(tx, rx)}
        self.assertEqual(diffs, {"STX": True, "Address": True, "Length": False, "Payload": False,
                                 "CRC": False, "ETX": True})

    def test_byte_diff(self):
        d = byte_diff(b"\x01\x02\x03", b"\x01\x05")
        self.assertEqual([x.same for x in d], [True, False, False])
        self.assertIsNone(d[2].b)


class DiffAnalyzerTests(unittest.TestCase):
    def test_spec_example_counter(self):
        pkts = [bytes.fromhex(f"0201100064010000{b:02X}00") for b in (0x20, 0x21, 0x22)]
        report = analyze_packets(pkts)
        varying = report.varying_offsets
        self.assertEqual([o.offset for o in varying], [8])
        self.assertEqual(varying[0].values, [0x20, 0x21, 0x22])
        self.assertEqual(varying[0].pattern, "Incrementing (+1)")
        self.assertTrue(varying[0].interpretation.startswith("Possible interpretation"))
        self.assertIn("Counter".lower(), varying[0].interpretation.lower())

    def test_classify(self):
        self.assertEqual(classify_sequence([5, 5, 5]), "Constant")
        self.assertEqual(classify_sequence([0xFE, 0xFF, 0x00]), "Incrementing (+1)")  # wraps
        self.assertEqual(classify_sequence([3, 2, 1]), "Decrementing (-1)")
        self.assertEqual(classify_sequence([0, 4, 8]), "Incrementing (step +4)")
        self.assertEqual(classify_sequence([1, 0, 1, 0]), "Alternating between two values")
        self.assertEqual(classify_sequence([9, 1, 200]), "Varying")

    def test_16bit_counter_region(self):
        pkts = [bytes([0xAA]) + v.to_bytes(2, "little") + b"\x55" for v in (0x00FE, 0x00FF, 0x0100)]
        report = analyze_packets(pkts, crc_search=False)
        self.assertTrue(any("16-bit little-endian" in r.pattern for r in report.regions), report.regions)

    def test_crc_verified_across_packets(self):
        proto = dextens()
        pkts = [encode_packet(proto, {"Address": 1, "Payload": f"cmd {i}"}) for i in range(4)]
        report = analyze_packets(pkts)
        self.assertIn("CRC-16/MODBUS", {m.algorithm for m in report.crc_matches})

    def test_different_lengths_noted(self):
        report = analyze_packets([b"\x01\x02", b"\x01\x02\x03"])
        self.assertTrue(any("lengths differ" in n for n in report.notes))
        self.assertEqual(report.offsets[2].pattern, "Absent in some packets")

    def test_needs_two_packets(self):
        self.assertTrue(analyze_packets([b"\x01"]).notes)


class HistoryTests(unittest.TestCase):
    def test_add_and_export(self):
        h = PacketHistory()
        h.add("TX", EXAMPLE_PACKET, "VALID", "Dextens USB Protocol", 'Payload="get diag"')
        h.add("RX", RX_PACKET, "CRC ERROR", "Dextens USB Protocol")
        self.assertEqual([e.length for e in h], [15, 14])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.csv"
            self.assertEqual(h.export_csv(path), 2)
            with path.open(encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        self.assertEqual(rows[0][:4], ["Time", "Direction", "Length", "Status"])
        self.assertEqual(rows[1][1:4], ["TX", "15", "VALID"])
        self.assertEqual(rows[2][3], "CRC ERROR")
        h.clear()
        self.assertEqual(len(h), 0)

    def test_max_entries(self):
        h = PacketHistory(max_entries=3)
        for i in range(5):
            h.add("TX", bytes([i]), "VALID", "p")
        self.assertEqual([e.data[0] for e in h], [2, 3, 4])


class FileExtractionTests(unittest.TestCase):
    def test_log_with_timestamps(self):
        text = ("12:32:10.123 TX: 02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03\n"
                "12:32:11 RX -> 02 01 07 00 64 69 61 67 3A 4F 4B DD BB 03\n"
                "some unrelated line\n")
        pkts = extract_packets(text)
        self.assertEqual(len(pkts), 2)
        self.assertEqual(pkts[0].data, EXAMPLE_PACKET)
        self.assertEqual((pkts[0].direction, pkts[1].direction), ("TX", "RX"))
        self.assertEqual(pkts[1].data, RX_PACKET)

    def test_csv_column(self):
        text = "time,dir,len,data\n2026-10-16 12:00:00,TX,15,02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03\n"
        pkts = extract_packets(text)
        self.assertEqual([p.data for p in pkts], [EXAMPLE_PACKET])

    def test_comma_separated_bytes_and_0x(self):
        self.assertEqual(extract_packets("02,01,08,00")[0].data, bytes.fromhex("02010800"))
        self.assertEqual(extract_packets("0x02 0x01")[0].data, b"\x02\x01")

    def test_intel_hex_record(self):
        pkts = extract_packets(":0300300002337A1E\n:00000001FF\n")
        self.assertEqual([p.data for p in pkts], [bytes.fromhex("02337A")])

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "p.log"
            txt.write_text("TX 02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03\n", encoding="utf-8")
            self.assertEqual(load_packets_from_file(txt)[0].data, EXAMPLE_PACKET)
            binf = Path(tmp) / "p.bin"
            binf.write_bytes(EXAMPLE_PACKET)
            self.assertEqual(load_packets_from_file(binf)[0].data, EXAMPLE_PACKET)


class AITests(unittest.TestCase):
    def test_heuristic_report_labels_inference(self):
        r = decode_packet(dextens(), EXAMPLE_PACKET)
        report = HeuristicAnalyzer().analyze(r, "TX")
        text = report.to_text()
        self.assertFalse(report.is_ai)
        self.assertIn("Payload: \"get diag\"", text)
        self.assertIn("Possible interpretation", text)
        self.assertIn("command frame", text)
        self.assertIn("not verified", text)
        self.assertIn("source of truth", text)

    def test_prompt_contains_decode(self):
        prompt = build_prompt(decode_packet(dextens(), EXAMPLE_PACKET), "TX")
        self.assertIn("get diag", prompt)
        self.assertIn("CRC VALID", prompt)

    def test_pluggable_backend(self):
        class Dummy(AIAnalyzer):
            name = "dummy"

            def is_available(self):
                return False

            def analyze(self, result, direction=""):
                raise AssertionError("must not be called")

        import ai
        ai.ANALYZERS["dummy"] = Dummy
        try:
            self.assertIsInstance(get_analyzer("dummy"), HeuristicAnalyzer)  # falls back when unavailable
        finally:
            del ai.ANALYZERS["dummy"]


class TransportTests(unittest.TestCase):
    def test_loopback(self):
        t = LoopbackTransport()
        received = []
        t.set_rx_callback(received.append)
        with self.assertRaises(TransportError):
            t.write(b"\x01")
        t.open()
        t.write(EXAMPLE_PACKET)
        self.assertEqual(received, [EXAMPLE_PACKET])
        self.assertTrue(decode_packet(dextens(), received[0]).ok)


if __name__ == "__main__":
    unittest.main()
