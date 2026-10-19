"""Decoder tests, centred on the reference packet from the specification."""
import unittest

import tests  # noqa: F401
from core.decoder import STATUS_CHECK_ERROR, STATUS_INVALID, STATUS_VALID, decode_packet
from core.field_decoder import UNDEFINED
from tests.helpers import EXAMPLE_PACKET, dextens, load_example, proto_from


def mutate(data: bytes, index: int, value: int) -> bytes:
    b = bytearray(data)
    b[index] = value
    return bytes(b)


class ReferencePacketTests(unittest.TestCase):
    """02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03 must decode correctly."""

    def setUp(self):
        self.result = decode_packet(dextens(), EXAMPLE_PACKET)

    def test_valid(self):
        self.assertEqual(self.result.errors, [])
        self.assertEqual(self.result.warnings, [])
        self.assertEqual(self.result.status, STATUS_VALID)
        self.assertEqual(self.result.status_message(), "✓ Packet valid")

    def test_field_values(self):
        r = self.result
        self.assertEqual(r.field("STX").raw, b"\x02")
        self.assertEqual(r.value("Address"), 1)
        self.assertEqual(r.value("Length"), 8)
        self.assertEqual(r.field("Length").raw, b"\x08\x00")
        self.assertEqual(r.value("Payload"), "get diag")
        self.assertEqual(r.field("CRC").raw, bytes.fromhex("DD1F"))
        self.assertEqual(r.field("ETX").raw, b"\x03")

    def test_offsets(self):
        layout = [(f.name, f.offset, f.length) for f in self.result.fields]
        self.assertEqual(layout, [("STX", 0, 1), ("Address", 1, 1), ("Length", 2, 2), ("Payload", 4, 8),
                                  ("CRC", 12, 2), ("ETX", 14, 1)])
        self.assertEqual(self.result.field("Payload").offset_text, "4-11")

    def test_crc_result(self):
        self.assertEqual(len(self.result.checks), 1)
        check = self.result.checks[0]
        self.assertTrue(check.valid)
        self.assertEqual(check.algorithm, "CRC-16/MODBUS")
        self.assertEqual(check.received, bytes.fromhex("DD1F"))
        self.assertEqual(check.calculated, bytes.fromhex("DD1F"))
        self.assertEqual((check.range_start, check.range_end), (1, 11))
        lines = check.summary_lines()
        self.assertIn("CRC Received  : DD 1F", lines)
        self.assertIn("CRC Calculated: DD 1F", lines)
        self.assertIn("✓ CRC VALID", lines)
        self.assertTrue(self.result.field("CRC").interpretation.startswith("VALID"))

    def test_all_fields_defined(self):
        self.assertTrue(all(f.defined for f in self.result.fields))

    def test_report_text(self):
        text = self.result.to_text()
        self.assertIn('"get diag"', text)
        self.assertIn("CRC VALID", text)


class InvalidPacketTests(unittest.TestCase):
    def setUp(self):
        self.proto = dextens()

    def test_crc_mismatch(self):
        bad = EXAMPLE_PACKET[:12] + bytes.fromhex("A327") + b"\x03"
        r = decode_packet(self.proto, bad)
        self.assertEqual(r.status, STATUS_CHECK_ERROR)
        self.assertFalse(r.checks[0].valid)
        self.assertEqual(r.checks[0].calculated, bytes.fromhex("DD1F"))
        self.assertIn("✗ CRC INVALID", r.checks[0].summary_lines())
        self.assertTrue(any("CRC mismatch" in e for e in r.errors))
        self.assertEqual(r.value("Payload"), "get diag")  # still decoded

    def test_payload_corruption_detected_by_crc(self):
        r = decode_packet(self.proto, mutate(EXAMPLE_PACKET, 5, ord("E")))
        self.assertEqual(r.status, STATUS_CHECK_ERROR)

    def test_missing_stx(self):
        r = decode_packet(self.proto, mutate(EXAMPLE_PACKET, 0, 0x05))
        self.assertTrue(any(e.startswith("Missing STX") for e in r.errors))
        self.assertEqual(r.status, STATUS_INVALID)

    def test_missing_etx(self):
        r = decode_packet(self.proto, mutate(EXAMPLE_PACKET, 14, 0x04))
        self.assertTrue(any(e.startswith("Missing ETX") for e in r.errors))

    def test_length_field_exceeds_packet(self):
        r = decode_packet(self.proto, mutate(EXAMPLE_PACKET, 2, 0x20))
        self.assertTrue(any("Length field exceeds packet size" in e for e in r.errors), r.errors)
        self.assertEqual(r.status, STATUS_INVALID)

    def test_packet_shorter_than_expected(self):
        r = decode_packet(self.proto, EXAMPLE_PACKET[:3])
        self.assertTrue(any("shorter than expected" in e or "exceeds packet size" in e for e in r.errors), r.errors)
        self.assertEqual(r.status, STATUS_INVALID)

    def test_length_smaller_than_payload_leaves_undefined_bytes(self):
        r = decode_packet(self.proto, mutate(EXAMPLE_PACKET, 2, 0x07))
        undefined = [f for f in r.fields if not f.defined]
        self.assertEqual(len(undefined), 1)
        self.assertEqual(undefined[0].display, UNDEFINED)
        self.assertFalse(r.ok)  # CRC is now read from the wrong place

    def test_trailing_bytes(self):
        proto = proto_from([{"name": "A", "type": "uint8"}, {"name": "B", "type": "uint8"}])
        r = decode_packet(proto, b"\x01\x02\x03\x04")
        self.assertTrue(r.ok)
        self.assertTrue(any("longer than expected" in w for w in r.warnings))
        tail = r.fields[-1]
        self.assertFalse(tail.defined)
        self.assertEqual((tail.offset, tail.length, tail.display), (2, 2, UNDEFINED))

    def test_empty_packet_and_no_protocol(self):
        self.assertIn("Empty packet", decode_packet(self.proto, b"").errors)
        self.assertIn("No protocol selected", decode_packet(None, b"\x01").errors)

    def test_single_byte_and_random_garbage_never_raise(self):
        import random
        rnd = random.Random(1234)
        for proto in (self.proto, load_example("Modbus RTU"), load_example("Simple Sensor Packet")):
            for n in range(0, 40):
                data = bytes(rnd.randrange(256) for _ in range(n))
                r = decode_packet(proto, data)
                self.assertIsNotNone(r.status)

    def test_invalid_protocol_definition(self):
        proto = proto_from([{"name": "P", "type": "ascii", "length_from": "Nope"}])
        r = decode_packet(proto, b"abc")
        self.assertTrue(any(e.startswith("Invalid protocol definition") for e in r.errors))

    def test_expected_value_mismatch(self):
        proto = load_example("Simple Sensor Packet")
        r = decode_packet(proto, bytes.fromhex("AB55010200EB01C2A556"))
        self.assertTrue(any("'Header' expected AA 55" in e for e in r.errors), r.errors)


class DynamicLengthTests(unittest.TestCase):
    def test_length_from_with_adjust_and_scale(self):
        proto = proto_from([
            {"name": "Len", "type": "uint8"},
            {"name": "Words", "type": "hex", "length_from": "Len", "length_scale": 2, "length_adjust": -1},
            {"name": "End", "type": "hex", "length": 1},
        ])
        r = decode_packet(proto, bytes.fromhex("02 AABBCC FF".replace(" ", "")))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.field("Words").raw, bytes.fromhex("AABBCC"))  # 2*2-1 = 3
        self.assertEqual(r.field("End").raw, b"\xff")

    def test_zero_length_payload(self):
        proto = dextens()
        from core.encoder import encode_packet
        pkt = encode_packet(proto, {"Address": 1, "Payload": ""})
        r = decode_packet(proto, pkt)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.value("Payload"), "")

    def test_remaining_length(self):
        modbus = load_example("Modbus RTU")
        r = decode_packet(modbus, bytes.fromhex("01030400E601C29BC5"))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.field("Data").raw, bytes.fromhex("0400E601C2"))
        self.assertEqual(r.field("Function Code").interpretation, "Read Holding Registers")

    def test_modbus_request_and_exception(self):
        modbus = load_example("Modbus RTU")
        self.assertTrue(decode_packet(modbus, bytes.fromhex("01030000000AC5CD")).ok)
        r = decode_packet(modbus, bytes.fromhex("018302C0F1"))
        self.assertTrue(r.ok, r.errors)
        self.assertTrue(r.field("Function Code").interpretation.startswith("Exception"))

    def test_negative_offset(self):
        proto = proto_from([
            {"name": "Head", "type": "uint8", "offset": 0},
            {"name": "Tail", "type": "uint16", "offset": -2},
        ])
        r = decode_packet(proto, bytes.fromhex("0155AABB"))
        self.assertEqual(r.value("Tail"), 0xAABB)
        gap = [f for f in r.fields if not f.defined]  # 0x55 at offset 1 is not described
        self.assertEqual([(f.offset, f.length) for f in gap], [(1, 1)])

    def test_length_from_little_endian_uint16(self):
        proto = proto_from([
            {"name": "Len", "type": "uint16", "endian": "little"},
            {"name": "Data", "type": "hex", "length_from": "Len"},
        ])
        r = decode_packet(proto, bytes.fromhex("0300AABBCC"))
        self.assertEqual(r.field("Data").raw, bytes.fromhex("AABBCC"))


class SensorExampleTests(unittest.TestCase):
    def test_sensor_packet(self):
        proto = load_example("Simple Sensor Packet")
        r = decode_packet(proto, bytes.fromhex("AA55010200EB01C2A555"))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.field("Temperature").display, "23.5 °C")
        self.assertEqual(r.field("Humidity").display, "45 %RH")
        bits = {b.name: b.value for b in r.field("Status").bits}
        self.assertEqual(bits["Fault"], 1)
        self.assertEqual(bits["Warning"], 0)
        self.assertEqual(r.checks[0].kind, "Checksum")
        self.assertTrue(r.checks[0].valid)

    def test_sensor_checksum_error(self):
        proto = load_example("Simple Sensor Packet")
        r = decode_packet(proto, bytes.fromhex("AA55010200EB01C2A556"))
        self.assertEqual(r.status, "CHECKSUM ERROR")


class ExampleFilesTests(unittest.TestCase):
    def test_example_packets_in_json_decode_as_labelled(self):
        for name in ("Dextens USB Protocol", "Modbus RTU", "Simple Sensor Packet"):
            proto = load_example(name)
            self.assertFalse(proto.errors(), proto.errors())
            for ex in proto.examples:
                r = decode_packet(proto, bytes.fromhex(ex.hex.replace(" ", "")))
                broken = any(w in ex.name.lower() for w in ("corrupt", "too large"))
                with self.subTest(protocol=name, example=ex.name):
                    self.assertEqual(r.ok, not broken, r.errors)


if __name__ == "__main__":
    unittest.main()
