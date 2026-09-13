"""Reversible, versioned shortlex codecs for code points, glyph IDs and bytes.

Unicode compatibility follows the pinned Domalec repository documented in
``research/conversion-mechanics.md``. No Unicode normalization is performed.
Decimal IDs are strings to avoid loss of precision in JSON consumers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Sequence

from .errors import ValidationError, BudgetExceeded

DEFAULT_ALPHABET = "".join(chr(code) for code in range(32, 127))
MAX_STRING_SYMBOLS = 4096
MAX_ALPHABET_SYMBOLS = 1024
MAX_BYTE_LENGTH = 65536


def decimal_string(value: int) -> str:
    """Format a natural without changing Python's process-wide digit limit."""
    if type(value) is not int or value < 0:
        raise ValidationError("Expected a nonnegative integer.")
    if value == 0:
        return "0"
    parts = []
    while value:
        value, remainder = divmod(value, 1_000_000_000)
        parts.append(remainder)
    return str(parts[-1]) + "".join(f"{part:09d}" for part in reversed(parts[:-1]))


def natural_number(value: str, max_digits: int = 200000) -> int:
    if not isinstance(value, str) or not value or len(value) > max_digits * 2 + 32:
        raise ValidationError("Expected a bounded nonnegative ASCII decimal string.")
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValidationError("Expected a nonnegative ASCII decimal integer string.")
    value = value.lstrip("0") or "0"
    if len(value) > max_digits:
        raise BudgetExceeded("Decimal ID exceeds the codec context budget.")
    result = 0
    for start in range(0, len(value), 9):
        part = value[start:start + 9]
        result = result * 10 ** len(part) + int(part)
    return result


def _utf8(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"{label} contains an invalid Unicode surrogate.") from exc


def _limit(value: int, maximum: int = MAX_BYTE_LENGTH) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValidationError(f"Context length must be an integer in 1..{maximum}.")
    return value


def _encode_digits(digits: Sequence[int], base: int) -> str:
    rank = 0
    for digit in digits:
        # The extra one at each step is the exact shorter-length bucket offset.
        rank = rank * base + digit + 1
    return decimal_string(rank)


def _decode_digits(value: str, base: int, limit: int) -> list[int]:
    remaining = natural_number(value, math.ceil(limit * math.log10(base)) + 2)
    output = []
    while remaining:
        if len(output) == limit:
            raise BudgetExceeded(f"ID exceeds the {limit}-symbol context limit.")
        remaining, digit = divmod(remaining - 1, base)
        output.append(digit)
    output.reverse()
    return output


class ShortlexCodec:
    """Ordered Unicode code-point alphabet; compatible with published GPIL IDs."""

    codec_id = "domalec-shortlex-repo-v1"
    version = 1

    def __init__(self, alphabet: str = DEFAULT_ALPHABET, max_length: int = MAX_STRING_SYMBOLS):
        _utf8(alphabet, "alphabet")
        if not 2 <= len(alphabet) <= MAX_ALPHABET_SYMBOLS:
            raise ValidationError("Alphabet must contain between 2 and 1024 symbols.")
        if len(set(alphabet)) != len(alphabet):
            raise ValidationError("Alphabet symbols must be unique.")
        self.alphabet = alphabet
        self.base = len(alphabet)
        self.indices = {symbol: index for index, symbol in enumerate(alphabet)}
        self.max_length = _limit(max_length, MAX_STRING_SYMBOLS)

    def encode(self, text: str) -> str:
        _utf8(text, "text")
        if len(text) > self.max_length:
            raise BudgetExceeded(f"Input exceeds the {self.max_length}-symbol context limit.")
        try:
            digits = [self.indices[symbol] for symbol in text]
        except KeyError as exc:
            raise ValidationError(f"Symbol {exc.args[0]!r} is outside the ordered alphabet.") from exc
        return _encode_digits(digits, self.base)

    def decode(self, decimal_id: str) -> str:
        return "".join(self.alphabet[digit] for digit in _decode_digits(decimal_id, self.base, self.max_length))

    def encode_verified(self, text: str) -> str:
        result = self.encode(text)
        if self.decode(result) != text:
            raise ValidationError("Shortlex reverse verification failed.")
        return result

    def manifest(self) -> dict:
        return {"codec_id": self.codec_id, "version": self.version,
                "alphabet": self.alphabet, "normalization": "none", "max_length": self.max_length,
                "alphabet_digest": hashlib.sha256(self.alphabet.encode("utf-8")).hexdigest()}


class GlyphRankCodec:
    """Shortlex over explicit stable glyph IDs; IDs need not be Unicode scalars."""

    codec_id = "pandr-glyph-shortlex-v1"
    version = 1

    def __init__(self, symbols: Sequence[str], max_length: int = MAX_STRING_SYMBOLS):
        if not isinstance(symbols, (list, tuple)) or not 2 <= len(symbols) <= MAX_ALPHABET_SYMBOLS:
            raise ValidationError("Provide an ordered list of 2..1024 glyph IDs.")
        for glyph_id in symbols:
            _utf8(glyph_id, "glyph ID")
            if not 1 <= len(glyph_id) <= 128:
                raise ValidationError("Glyph IDs must contain 1..128 code points.")
        if len(set(symbols)) != len(symbols):
            raise ValidationError("Glyph IDs must be unique.")
        self.symbols = list(symbols)
        self.alphabet = self.symbols
        self.base = len(symbols)
        self.indices = {symbol: index for index, symbol in enumerate(symbols)}
        self.max_length = _limit(max_length, MAX_STRING_SYMBOLS)

    def encode(self, symbols: Sequence[str]) -> str:
        if not isinstance(symbols, (list, tuple)):
            raise ValidationError("A glyph sequence is an ordered list of glyph IDs.")
        if len(symbols) > self.max_length:
            raise BudgetExceeded("Glyph sequence exceeds the codec context budget.")
        try:
            digits = [self.indices[symbol] for symbol in symbols]
        except (KeyError, TypeError) as exc:
            raise ValidationError("Glyph sequence contains an unknown glyph ID.") from exc
        return _encode_digits(digits, self.base)

    def decode(self, decimal_id: str) -> list[str]:
        return [self.symbols[digit] for digit in _decode_digits(decimal_id, self.base, self.max_length)]

    def encode_verified(self, symbols: Sequence[str]) -> str:
        result = self.encode(symbols)
        if self.decode(result) != list(symbols):
            raise ValidationError("Glyph rank reverse verification failed.")
        return result

    def manifest(self) -> dict:
        ordered = json.dumps(self.symbols, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return {"codec_id": self.codec_id, "version": self.version, "symbols": self.symbols[:],
                "max_length": self.max_length, "symbols_digest": hashlib.sha256(ordered).hexdigest()}


def rank_bytes(data: bytes, max_length: int = MAX_BYTE_LENGTH) -> str:
    """Rank raw bytes, including empty files and leading NULs, without text decoding."""
    limit = _limit(max_length)
    if not isinstance(data, bytes):
        raise ValidationError("Byte codec input must be bytes.")
    if len(data) > limit:
        raise BudgetExceeded("Byte input exceeds the byte-codec context limit.")
    return _encode_digits(data, 256)


def unrank_bytes(decimal_id: str, max_length: int = MAX_BYTE_LENGTH) -> bytes:
    return bytes(_decode_digits(decimal_id, 256, _limit(max_length)))
