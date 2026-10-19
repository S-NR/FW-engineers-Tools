import unittest

import tests  # noqa: F401
from utils.ascii_utils import escape_bytes, hexdump_text, to_ascii
from utils.hex_utils import HexParseError, format_hex, parse_hex, parse_int, try_parse_hex

EXPECTED = bytes([0x02, 0x01, 0x08, 0x00])


class HexParsingTests(unittest.TestCase):
    def test_space_separated(self):
        self.assertEqual(parse_hex("02 01 08 00"), EXPECTED)

    def test_0x_prefixed(self):
        self.assertEqual(parse_hex("0x02 0x01 0x08 0x00"), EXPECTED)
        self.assertEqual(parse_hex("0X02,0X01,0x08,0x00"), EXPECTED)

    def test_comma_separated(self):
        self.assertEqual(parse_hex("02,01,08,00"), EXPECTED)
        self.assertEqual(parse_hex("02, 01, 08, 00"), EXPECTED)

    def test_multiline_and_comments(self):
        text = "02 01   # STX, address\n08 00 // length\n\n"
        self.assertEqual(parse_hex(text), EXPECTED)

    def test_contiguous_and_mixed_case(self):
        self.assertEqual(parse_hex("02010800"), EXPECTED)
        self.assertEqual(parse_hex("dd 1F Aa"), bytes([0xDD, 0x1F, 0xAA]))

    def test_c_escape_and_colon(self):
        self.assertEqual(parse_hex(r"\x02\x01\x08\x00"), EXPECTED)
        self.assertEqual(parse_hex("02:01:08:00"), EXPECTED)

    def test_single_digit_with_prefix_is_padded(self):
        self.assertEqual(parse_hex("0x2 0x1"), bytes([2, 1]))

    def test_empty(self):
        for text in ("", "   ", "\n\n", "# only a comment"):
            with self.assertRaises(HexParseError) as cm:
                parse_hex(text)
            self.assertIn("Empty", str(cm.exception))

    def test_invalid_character(self):
        with self.assertRaises(HexParseError) as cm:
            parse_hex("02 0G 03")
        self.assertIn("invalid character 'G'", str(cm.exception))
        self.assertIn("column 4", str(cm.exception))

    def test_odd_number_of_digits(self):
        with self.assertRaises(HexParseError) as cm:
            parse_hex("02 1 03")
        self.assertIn("Odd number of HEX characters", str(cm.exception))
        with self.assertRaises(HexParseError):
            parse_hex("020")

    def test_invalid_byte_value(self):
        with self.assertRaises(HexParseError) as cm:
            parse_hex("0x123")
        self.assertIn("Invalid byte value", str(cm.exception))
        with self.assertRaises(HexParseError):
            parse_hex("0x")

    def test_try_parse(self):
        self.assertEqual(try_parse_hex("02"), (b"\x02", None))
        data, err = try_parse_hex("zz")
        self.assertIsNone(data)
        self.assertTrue(err)

    def test_format(self):
        self.assertEqual(format_hex(b"\x02\xdd"), "02 DD")
        self.assertEqual(format_hex(b"\x02\xdd", sep=""), "02DD")

    def test_parse_int(self):
        self.assertEqual(parse_int("0x1021"), 0x1021)
        self.assertEqual(parse_int("4129"), 4129)
        self.assertEqual(parse_int("0b101"), 5)
        self.assertEqual(parse_int("010"), 10)
        self.assertIsNone(parse_int(""))
        with self.assertRaises(ValueError):
            parse_int("abc")


class AsciiTests(unittest.TestCase):
    def test_non_printable_as_dot(self):
        self.assertEqual(to_ascii(bytes.fromhex("0201676574200364")), "..get .d")

    def test_escape(self):
        self.assertEqual(escape_bytes(b"ok\r\n\x00"), "ok\\r\\n\\x00")

    def test_hexdump(self):
        dump = hexdump_text(b"get diag")
        self.assertIn("67 65 74 20 64 69 61 67", dump)
        self.assertIn("|get diag|", dump)


if __name__ == "__main__":
    unittest.main()
