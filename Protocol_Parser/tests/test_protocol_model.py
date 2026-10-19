import json
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from core.protocol import FieldDefinition, ProtocolDefinition, ProtocolError, normalize_type
from protocols.protocol_manager import ProtocolManager
from tests.helpers import dextens, proto_from

SPEC_JSON = {
    "name": "Spec Example",
    "frame": {"start_byte": "02", "end_byte": "03"},
    "fields": [
        {"name": "Address", "offset": 1, "length": 1, "type": "uint8"},
        {"name": "Length", "offset": 2, "length": 1, "type": "uint8"},
        {"name": "Payload", "offset": 3, "length_from": "Length", "type": "ascii"},
    ],
}


class ModelTests(unittest.TestCase):
    def test_spec_style_json_loads(self):
        p = ProtocolDefinition.from_dict(SPEC_JSON)
        self.assertEqual(p.frame.start_bytes, b"\x02")
        self.assertEqual(p.fields[2].length_from, "Length")
        self.assertEqual(p.errors(), [])

    def test_round_trip(self):
        original = dextens()
        again = ProtocolDefinition.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(again.to_dict(), original.to_dict())

    def test_unknown_keys_preserved(self):
        d = dict(SPEC_JSON, future_option={"x": 1})
        d["fields"] = [dict(SPEC_JSON["fields"][0], color="red")] + SPEC_JSON["fields"][1:]
        out = ProtocolDefinition.from_dict(d).to_dict()
        self.assertEqual(out["future_option"], {"x": 1})
        self.assertEqual(out["fields"][0]["color"], "red")

    def test_type_aliases(self):
        self.assertEqual(normalize_type("UINT16"), "uint16")
        self.assertEqual(normalize_type("UTF-8"), "utf8")
        self.assertEqual(normalize_type("Bit field"), "bitfield")
        self.assertEqual(normalize_type("Boolean"), "bool")
        f = FieldDefinition.from_dict({"name": "C", "type": "CRC16"})
        self.assertEqual((f.type, f.check.algorithm), ("crc", "CRC-16"))
        f = FieldDefinition.from_dict({"name": "C", "type": "crc32"})
        self.assertEqual(f.fixed_size(), 4)

    def test_offset_keywords(self):
        self.assertEqual(FieldDefinition.from_dict({"name": "a", "offset": "Dynamic"}).offset, "auto")
        self.assertEqual(FieldDefinition.from_dict({"name": "a", "offset": "Last", "length": 1}).offset, "last")
        self.assertEqual(FieldDefinition.from_dict({"name": "a", "offset": "0x10", "length": 1}).offset, 16)

    def test_invalid_definitions_raise(self):
        with self.assertRaises(ProtocolError):
            ProtocolDefinition.from_dict({"fields": []})  # no name
        with self.assertRaises(ProtocolError):
            ProtocolDefinition.from_dict({"name": "x", "fields": "nope"})
        with self.assertRaises(ProtocolError):
            ProtocolDefinition.from_dict({"name": "x", "fields": [{"name": "a", "endian": "middle"}]})
        with self.assertRaises(ProtocolError):
            ProtocolDefinition.from_dict({"name": "x", "fields": [{"name": "a", "offset": "somewhere"}]})


class ValidationTests(unittest.TestCase):
    def errors(self, fields):
        return proto_from(fields).errors()

    def test_unsupported_type(self):
        self.assertTrue(any("unsupported field type" in e for e in self.errors([{"name": "a", "type": "vector"}])))

    def test_duplicate_names(self):
        errs = self.errors([{"name": "a", "type": "uint8"}, {"name": "a", "type": "uint8"}])
        self.assertTrue(any("Duplicate" in e for e in errs))

    def test_length_from_must_be_earlier_integer(self):
        self.assertTrue(self.errors([{"name": "p", "type": "hex", "length_from": "n"},
                                     {"name": "n", "type": "uint8"}]))
        self.assertTrue(self.errors([{"name": "n", "type": "ascii", "length": 1},
                                     {"name": "p", "type": "hex", "length_from": "n"}]))

    def test_size_mismatch(self):
        self.assertTrue(self.errors([{"name": "a", "type": "uint16", "length": 3}]))
        self.assertTrue(self.errors([{"name": "c", "type": "crc", "length": 1, "crc": {"algorithm": "CRC-16"}}]))

    def test_variable_type_needs_length(self):
        self.assertTrue(self.errors([{"name": "a", "type": "ascii"}]))

    def test_bits_out_of_range(self):
        self.assertTrue(self.errors([{"name": "s", "type": "bitfield", "length": 1,
                                      "bits": [{"name": "x", "start": 8}]}]))

    def test_remaining_followed_by_variable(self):
        self.assertTrue(self.errors([{"name": "d", "type": "hex", "length": "remaining"},
                                     {"name": "e", "type": "ascii", "length": "remaining"}]))

    def test_unknown_check_range_field(self):
        self.assertTrue(self.errors([{"name": "a", "type": "uint8"},
                                     {"name": "c", "type": "crc", "crc": {"algorithm": "CRC-8",
                                                                          "range_start": "zzz"}}]))


class ManagerTests(unittest.TestCase):
    def test_examples_load(self):
        m = ProtocolManager()
        m.reload()
        for name in ("Dextens USB Protocol", "Modbus RTU", "Simple Sensor Packet"):
            self.assertIn(name, m.names())

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = ProtocolManager(search_dirs=[Path(tmp)], user_dir=Path(tmp))
            p = dextens()
            p.source_path = None
            p.name = "Copy Of Dextens"
            path = m.save(p)
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "copy_of_dextens.json")
            m2 = ProtocolManager(search_dirs=[Path(tmp)], user_dir=Path(tmp))
            m2.reload()
            self.assertEqual(m2.get("Copy Of Dextens").to_dict(), p.to_dict())

    def test_broken_file_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text("{ not json", encoding="utf-8")
            (Path(tmp) / "bad2.json").write_text('{"name": "x"}', encoding="utf-8")
            m = ProtocolManager(search_dirs=[Path(tmp)], user_dir=Path(tmp))
            m.reload()
            self.assertEqual(m.names(), [])
            self.assertEqual(len(m.load_errors), 2)


if __name__ == "__main__":
    unittest.main()
