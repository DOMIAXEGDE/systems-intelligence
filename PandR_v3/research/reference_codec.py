"""Offline reference for the website-linked repository's shortlex codec.

This reproduces published SequentialStringId.php, not an assertion about the
current private deployed PHP. See conversion-mechanics.md and repository-LICENSE.
Run: python reference_codec.py --self-check
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_STRING_SYMBOLS = 4096
MAX_ALPHABET_SYMBOLS = 1024
DEFAULT_ALPHABET = "".join(chr(code) for code in range(32, 127))


def _decimal(value: int) -> str:
    """Format arbitrary precision without changing Python's global digit limit."""
    if value == 0:
        return "0"
    chunks = []
    while value:
        value, remainder = divmod(value, 1_000_000_000)
        chunks.append(remainder)
    return str(chunks[-1]) + "".join(f"{part:09d}" for part in reversed(chunks[:-1]))


def _natural(value: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("Expected a non-negative ASCII decimal integer string.")
    value = value.lstrip("0") or "0"
    result = 0
    for position in range(0, len(value), 9):
        chunk = value[position:position + 9]
        result = result * 10 ** len(chunk) + int(chunk)
    return result


def _utf8(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be valid UTF-8.") from error


class ShortlexCodec:
    """Ordered code-point alphabet and reversible shortlex ordinal mapping."""

    def __init__(self, alphabet: str = DEFAULT_ALPHABET):
        _utf8(alphabet, "alphabet")
        if not 2 <= len(alphabet) <= MAX_ALPHABET_SYMBOLS:
            raise ValueError("Alphabet must contain between 2 and 1024 symbols.")
        if len(set(alphabet)) != len(alphabet):
            raise ValueError("Alphabet symbols must be unique.")
        self.alphabet = alphabet
        self.base = len(alphabet)
        self.indices = {symbol: index for index, symbol in enumerate(alphabet)}

    def encode(self, text: str) -> str:
        _utf8(text, "text")
        if len(text) > MAX_STRING_SYMBOLS:
            raise ValueError("Input exceeds the 4096-symbol context limit.")
        if not text:
            return "0"
        rank = 0
        for position, symbol in enumerate(text, 1):
            if symbol not in self.indices:
                raise ValueError(f"Symbol at position {position} is outside the alphabet.")
            rank = rank * self.base + self.indices[symbol]
        shorter_count = (self.base ** len(text) - self.base) // (self.base - 1)
        return _decimal(1 + shorter_count + rank)

    def decode(self, decimal_id: str) -> str:
        remaining = _natural(decimal_id)
        if remaining == 0:
            return ""
        length, bucket = 1, self.base
        while remaining > bucket:
            remaining -= bucket
            length += 1
            if length > MAX_STRING_SYMBOLS:
                raise ValueError("ID exceeds the 4096-symbol context limit.")
            bucket *= self.base
        offset = remaining - 1
        output = [self.alphabet[0]] * length
        for position in range(length - 1, -1, -1):
            offset, digit = divmod(offset, self.base)
            output[position] = self.alphabet[digit]
        return "".join(output)

    def encode_verified(self, text: str) -> str:
        decimal_id = self.encode(text)
        if self.decode(decimal_id) != text:
            raise ArithmeticError("Round-trip verification failed.")
        return decimal_id


def self_check() -> dict[str, int | str]:
    directory = Path(__file__).resolve().parent
    vectors = json.loads((directory / "codec-vectors.json").read_text(encoding="utf-8"))
    count = 0
    for vector in vectors["roundtrip"]:
        codec = ShortlexCodec(vector["alphabet"])
        assert codec.encode_verified(vector["text"]) == vector["id"], vector["name"]
        assert codec.decode(vector["id"]) == vector["text"], vector["name"]
        count += 1
    for vector in vectors["decode_aliases"]:
        codec = ShortlexCodec(vector["alphabet"])
        decoded = codec.decode(vector["input_id"])
        assert decoded == vector["text"], vector["name"]
        assert codec.encode(decoded) == vector["canonical_id"], vector["name"]
        count += 1
    for vector in vectors["rejections"]:
        try:
            codec = ShortlexCodec(vector["alphabet"])
            if vector["operation"] == "encode":
                codec.encode(vector["input"])
            elif vector["operation"] == "decode":
                codec.decode(vector["input"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected rejection: {vector['name']}")
        count += 1
    published = json.loads((directory / "repository-data-events.json").read_text(encoding="utf-8"))
    for event in published["events"]:
        codec = ShortlexCodec(event["alphabet"])
        assert codec.encode_verified(event["value"]) == event["sequential-string ID"], event["label"]
        assert codec.decode(event["sequential-string ID"]) == event["value"], event["label"]
    # Meaningful boundary: 1024 symbols and maximum length produce >4300 digits.
    wide = ShortlexCodec("".join(chr(code) for code in range(1024)))
    boundary = wide.alphabet[-1] * MAX_STRING_SYMBOLS
    boundary_id = wide.encode_verified(boundary)
    assert len(boundary_id) > 4300
    for operation in (lambda: wide.encode(boundary + wide.alphabet[-1]),
                      lambda: wide.decode(_decimal(_natural(boundary_id) + 1)),
                      lambda: ShortlexCodec("a\ud800")):
        try:
            operation()
        except ValueError:
            pass
        else:
            raise AssertionError("Boundary validation failed.")
    return {"status": "passed", "php_vectors": count,
            "published_records": len(published["events"]),
            "max_length_roundtrip_decimal_digits": len(boundary_id),
            "additional_boundary_checks": 4}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alphabet", default=DEFAULT_ALPHABET)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--encode")
    action.add_argument("--decode")
    action.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check(), ensure_ascii=True))
    else:
        codec = ShortlexCodec(args.alphabet)
        value = codec.encode_verified(args.encode) if args.encode is not None else codec.decode(args.decode)
        print(json.dumps(value, ensure_ascii=True))


if __name__ == "__main__":
    main()
