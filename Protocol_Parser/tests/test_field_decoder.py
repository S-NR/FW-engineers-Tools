import math
import struct
import unittest

import tests  # noqa: F401
from core.field_decoder import (UNDEFINED, decode_field_value, decode_float, decode_int, decode_text, decode_uint,
                                extract_bits, to_binary_string)
from core.protocol import BitDefinition, FieldDefinition


def fd(type_, **kw) -> FieldDefinition:
    return FieldDefinition(name="F", type=type_, **kw)


class EndiannessTests(unittest.TestCase):
    def test_uint16_spec_example(self):
        raw = bytes([0x34, 0x12])
        self.assertEqual(decode_uint(raw, "little"), 0x1234)
        self.assertEqual(decode_uint(raw, "little"), 4660)
        self.assertEqual(decode_uint(raw, "big"), 0x3412)
        self.assertEqual(decode_uint(raw, "big"), 13330)

    def test_uint32_both_orders(self):
        raw = bytes.fromhex("78563412")
        self.assertEqual(decode_uint(raw, "little"), 0x12345678)
        self.assertEqual(decode_uint(raw, "big"), 0x78563412)

    def test_field_respects_endian(self):
        self.assertEqual(decode_field_value(fd("uint16"), b"\x34\x12", "little").value, 4660)
        self.assertEqual(decode_field_value(fd("uint16"), b"\x34\x12", "big").value, 13330)


class IntegerTests(unittest.TestCase):
    def test_unsigned_types(self):
        self.assertEqual(decode_field_value(fd("uint8"), b"\xff", "big").value, 255)
        self.assertEqual(decode_field_value(fd("uint32"), b"\xff\xff\xff\xff", "big").value, 0xFFFFFFFF)
        self.assertEqual(decode_field_value(fd("uint64"), bytes(7) + b"\x01", "big").value, 1)

    def test_signed_types(self):
        self.assertEqual(decode_field_value(fd("int8"), b"\xff", "big").value, -1)
        self.assertEqual(decode_field_value(fd("int8"), b"\x80", "big").value, -128)
        self.assertEqual(decode_field_value(fd("int16"), b"\xfe\xff", "little").value, -2)
        self.assertEqual(decode_field_value(fd("int32"), b"\xff\xff\xff\x7f", "little").value, 0x7FFFFFFF)
        self.assertEqual(decode_field_value(fd("int64"), b"\xff" * 8, "big").value, -1)
        self.assertEqual(decode_int(b"\x00\x80", "little"), -32768)

    def test_wrong_size_is_reported_not_raised(self):
        out = decode_field_value(fd("uint16"), b"\x01", "big")
        self.assertTrue(out.errors)
        self.assertEqual(out.display, UNDEFINED)

    def test_value_names(self):
        f = fd("uint8", values={3: "Read Holding Registers"})
        self.assertEqual(decode_field_value(f, b"\x03", "big").interpretation, "Read Holding Registers")
        self.assertIn("Undefined value 7", decode_field_value(f, b"\x07", "big").interpretation)

    def test_scale_and_unit(self):
        f = fd("int16", scale=0.1, unit="°C")
        out = decode_field_value(f, b"\x00\xeb", "big")
        self.assertEqual(out.value, 235)
        self.assertEqual(out.display, "23.5 °C")
        out = decode_field_value(f, b"\xff\x9c", "big")  # -100 -> -10.0
        self.assertEqual(out.display, "-10 °C")


class FloatTests(unittest.TestCase):
    def test_float32(self):
        raw_le = struct.pack("<f", 3.5)
        self.assertEqual(decode_float(raw_le, "little"), 3.5)
        self.assertEqual(decode_float(struct.pack(">f", -1.25), "big"), -1.25)
        out = decode_field_value(fd("float32"), bytes.fromhex("40490FDB"), "big")
        self.assertAlmostEqual(out.value, math.pi, places=6)

    def test_float64(self):
        raw = struct.pack("<d", 1234.5678)
        self.assertEqual(decode_field_value(fd("float64"), raw, "little").value, 1234.5678)

    def test_nan_is_warning(self):
        out = decode_field_value(fd("float32"), bytes.fromhex("7FC00000"), "big")
        self.assertTrue(math.isnan(out.value))
        self.assertTrue(out.warnings)


class TextTests(unittest.TestCase):
    def test_ascii(self):
        out = decode_field_value(fd("ascii", length=8), b"get diag", "big")
        self.assertEqual(out.value, "get diag")
        self.assertEqual(out.display, '"get diag"')
        self.assertFalse(out.warnings)

    def test_ascii_invalid_bytes_warn(self):
        text, warnings = decode_text(b"ok\xff", "ascii")
        self.assertEqual(text, "ok\\xff")
        self.assertTrue(warnings)

    def test_control_chars_visible(self):
        out = decode_field_value(fd("ascii", length=4), b"OK\r\n", "big")
        self.assertEqual(out.display, '"OK\\r\\n"')

    def test_null_terminated(self):
        text, _ = decode_text(b"abc\x00\x00", "ascii", null_terminated=True)
        self.assertEqual(text, "abc")

    def test_utf8(self):
        out = decode_field_value(fd("utf8", length=6), "°C ok".encode(), "big")
        self.assertEqual(out.value, "°C ok")


class BinaryBitTests(unittest.TestCase):
    def test_binary(self):
        self.assertEqual(to_binary_string(b"\xa5\x01"), "10100101 00000001")

    def test_extract_bits(self):
        self.assertEqual(extract_bits(0xA5, 7, 1), 1)
        self.assertEqual(extract_bits(0xA5, 6, 1), 0)
        self.assertEqual(extract_bits(0xA5, 4, 4), 0xA)

    def test_bitfield_spec_example(self):
        f = fd("bitfield", length=1, bits=[
            BitDefinition("Fault", 7), BitDefinition("Warning", 6), BitDefinition("Ready", 5),
            BitDefinition("Sensor active", 2), BitDefinition("Communication OK", 0),
            BitDefinition("Mode", 3, 2, values={0: "Idle", 1: "Normal"}),
        ])
        out = decode_field_value(f, b"\xa5", "big")
        bits = {b.name: b for b in out.bits}
        self.assertEqual(bits["Fault"].value, 1)
        self.assertEqual(bits["Warning"].value, 0)
        self.assertEqual(bits["Ready"].value, 1)
        self.assertEqual(bits["Sensor active"].value, 1)
        self.assertEqual(bits["Communication OK"].value, 1)
        self.assertEqual(bits["Mode"].value, 0)
        self.assertEqual(bits["Mode"].interpretation, "Idle")
        self.assertEqual([b.start for b in out.bits], [7, 6, 5, 3, 2, 0])  # MSB first

    def test_bits_on_multi_byte_le(self):
        f = fd("uint16", bits=[BitDefinition("High", 15), BitDefinition("Low", 0)])
        out = decode_field_value(f, b"\x01\x80", "little")  # 0x8001
        self.assertEqual({b.name: b.value for b in out.bits}, {"High": 1, "Low": 1})

    def test_bool(self):
        self.assertIs(decode_field_value(fd("bool"), b"\x00", "big").value, False)
        out = decode_field_value(fd("bool"), b"\x02", "big")
        self.assertIs(out.value, True)
        self.assertTrue(out.warnings)

    def test_unsupported_type(self):
        out = decode_field_value(fd("quaternion"), b"\x00", "big")
        self.assertIn("Unsupported field type", out.errors[0])
        self.assertEqual(out.display, UNDEFINED)


if __name__ == "__main__":
    unittest.main()
