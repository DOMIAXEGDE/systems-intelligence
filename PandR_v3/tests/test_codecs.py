"""Independent golden records, reversibility and context-boundary coverage."""

import json
from pathlib import Path
import unittest

from pandr.codecs import (ShortlexCodec, GlyphRankCodec, rank_bytes, unrank_bytes,
                          decimal_string, natural_number)
from pandr.errors import ValidationError, BudgetExceeded

RESEARCH = Path(__file__).resolve().parents[1] / "research"


class CodecTests(unittest.TestCase):
    def test_all_35_published_php_vectors(self):
        data = json.loads((RESEARCH / "codec-vectors.json").read_text(encoding="utf-8"))
        count = 0
        for record in data["roundtrip"]:
            with self.subTest(name=record["name"]):
                codec = ShortlexCodec(record["alphabet"])
                self.assertEqual(codec.encode_verified(record["text"]), record["id"])
                self.assertEqual(codec.decode(record["id"]), record["text"])
            count += 1
        for record in data["decode_aliases"]:
            with self.subTest(name=record["name"]):
                codec = ShortlexCodec(record["alphabet"])
                self.assertEqual(codec.decode(record["input_id"]), record["text"])
                self.assertEqual(codec.encode(record["text"]), record["canonical_id"])
            count += 1
        for record in data["rejections"]:
            with self.subTest(name=record["name"]), self.assertRaises(ValidationError):
                codec = ShortlexCodec(record["alphabet"])
                getattr(codec, record["operation"])(record["input"])
            count += 1
        self.assertEqual(count, 35)

    def test_all_209_independent_published_records(self):
        records = json.loads((RESEARCH / "repository-data-events.json").read_text(encoding="utf-8"))["events"]
        self.assertEqual(len(records), 209)
        for record in records:
            with self.subTest(label=record["label"]):
                codec = ShortlexCodec(record["alphabet"])
                self.assertEqual(codec.encode_verified(record["value"]), record["sequential-string ID"])
                self.assertEqual(codec.decode(record["sequential-string ID"]), record["value"])

    def test_maximum_12331_decimal_digit_id_without_global_limit_change(self):
        codec = ShortlexCodec("".join(chr(code) for code in range(1024)))
        text = codec.alphabet[-1] * 4096
        rank = codec.encode_verified(text)
        self.assertEqual(len(rank), 12331)
        with self.assertRaises(BudgetExceeded):
            codec.encode(text + codec.alphabet[-1])
        with self.assertRaises(BudgetExceeded):
            codec.decode(decimal_string(natural_number(rank) + 1))

    def test_glyph_boundaries_order_and_empty(self):
        codec = GlyphRankCodec(["custom-mark-one", "custom-mark-two"])
        self.assertEqual(codec.encode([]), "0")
        self.assertEqual(codec.encode(["custom-mark-one", "custom-mark-two"]), "4")
        self.assertEqual(codec.decode("0004"), ["custom-mark-one", "custom-mark-two"])
        reversed_codec = GlyphRankCodec(["custom-mark-two", "custom-mark-one"])
        self.assertEqual(reversed_codec.encode(codec.decode("4")), "5")
        with self.assertRaises(ValidationError):
            codec.encode("custom-mark-one")
        with self.assertRaises(ValidationError):
            codec.encode(["unknown"])

    def test_arbitrary_bytes_preserve_nulls_and_non_utf8(self):
        for data in (b"", b"\0", b"\0\0", b"\0\xff\x80\n", bytes(range(256))):
            with self.subTest(data=data):
                self.assertEqual(unrank_bytes(rank_bytes(data)), data)
        self.assertEqual(rank_bytes(b"\0"), "1")
        self.assertEqual(rank_bytes(b"\0\0"), "257")
        with self.assertRaises(BudgetExceeded):
            rank_bytes(b"abc", max_length=2)
        with self.assertRaises(ValidationError):
            unrank_bytes("+1")


if __name__ == "__main__":
    unittest.main()
