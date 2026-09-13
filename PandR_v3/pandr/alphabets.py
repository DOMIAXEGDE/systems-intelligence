"""Original pixel alphabets: deterministic enumeration, edits and export maps."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .codecs import GlyphRankCodec
from .errors import ValidationError, BudgetExceeded

MAX_GLYPH_PIXELS = 1_048_576
MAX_SYMBOLS = 1024
MAX_SIDE = 128


def _integer(value, label, low=1, high=MAX_SIDE):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{label} must be an integer in {low}..{high}.")
    return value


def alphabet_digest(manifest: dict) -> str:
    value = {key: item for key, item in manifest.items() if key != "digest"}
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValidationError("Alphabet manifest must contain valid JSON data.") from exc
    return hashlib.sha256(data).hexdigest()


def _pixels(rows, width, height):
    if not isinstance(rows, list) or len(rows) != height:
        raise ValidationError("Glyph must have exactly height pixel rows.")
    if any(not isinstance(row, str) or len(row) != width or set(row) - {"0", "1"} for row in rows):
        raise ValidationError("Glyph rows must contain exactly width binary 0/1 pixels.")


def validate_alphabet(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValidationError("Alphabet manifest must be an object.")
    if not isinstance(manifest.get("id"), str) or not 1 <= len(manifest["id"]) <= 128:
        raise ValidationError("Alphabet ID must contain 1..128 characters.")
    _integer(manifest.get("version"), "version", high=1_000_000_000)
    width = _integer(manifest.get("width"), "width")
    height = _integer(manifest.get("height"), "height")
    symbols = manifest.get("symbols")
    GlyphRankCodec(symbols)
    if width * height * len(symbols) > MAX_GLYPH_PIXELS:
        raise BudgetExceeded("Alphabet exceeds the total glyph-pixel budget.")
    glyphs = manifest.get("glyphs")
    if not isinstance(glyphs, dict) or set(glyphs) != set(symbols):
        raise ValidationError("Glyph data keys must exactly match the ordered symbol IDs.")
    seen = set()
    for glyph_id in symbols:
        rows = glyphs[glyph_id]
        _pixels(rows, width, height)
        bitmap = tuple(rows)
        if bitmap in seen:
            raise ValidationError("Strict reversible alphabets require distinct glyph bitmaps.")
        seen.add(bitmap)
    if manifest.get("digest") != alphabet_digest(manifest):
        raise ValidationError("Alphabet digest does not match its manifest content.")
    return manifest


def define_alphabet(alphabet_id: str, glyphs: dict[str, list[str]], *, width: int, height: int,
                    symbols: list[str] | None = None, version: int = 1, **metadata) -> dict:
    """Define every bitmap explicitly; insertion order is retained unless supplied."""
    if not isinstance(glyphs, dict):
        raise ValidationError("Glyphs must be a dictionary of pixel rows.")
    _integer(width, "width")
    _integer(height, "height")
    if symbols is not None and not isinstance(symbols, list):
        raise ValidationError("Alphabet order must be a list of glyph IDs.")
    manifest = {"schema": "pandr-alphabet/1", "id": alphabet_id, "version": version,
                "symbols": list(glyphs) if symbols is None else list(symbols),
                "width": width, "height": height, "glyphs": deepcopy(glyphs),
                "color_mode": "1", "palette": [[255, 255, 255, 255], [28, 64, 216, 255]],
                "metrics": {"baseline": height - 1, "advance": width + 1},
                "metadata": deepcopy(metadata)}
    manifest["digest"] = alphabet_digest(manifest)
    return validate_alphabet(manifest)


def create_alphabet(alphabet_id: str = "original", count: int = 95, width: int = 10, height: int = 10) -> dict:
    """Enumerate bits row-major inside a fixed border and top-left orientation mark.

    The top-left pixel is zero; all other border pixels are one. Interior bit j
    is ``(glyph_index >> j) & 1``. No font or pre-existing letter is rasterized.
    """
    _integer(count, "count", low=2, high=MAX_SYMBOLS)
    _integer(width, "width", low=3)
    _integer(height, "height", low=3)
    interior = (width - 2) * (height - 2)
    if count > 2 ** interior:
        raise ValidationError("Requested alphabet exceeds the distinct interior-bitmap capacity.")
    if width * height * count > MAX_GLYPH_PIXELS:
        raise BudgetExceeded("Alphabet exceeds the total glyph-pixel budget.")
    glyphs = {}
    for index in range(count):
        rows = []
        for y in range(height):
            row = []
            for x in range(width):
                if x in (0, width - 1) or y in (0, height - 1):
                    bit = 0 if (x, y) == (0, 0) else 1
                else:
                    bit = (index >> ((y - 1) * (width - 2) + x - 1)) & 1
                row.append(str(bit))
            rows.append("".join(row))
        glyphs[f"g{index:04d}"] = rows
    return define_alphabet(alphabet_id, glyphs, width=width, height=height,
                           generation_rule="pandr-interior-bits-v1", scan_order="row-major-lsb-first")


def edit_glyph(manifest: dict, glyph_id: str, pixels: list[str]) -> dict:
    validate_alphabet(manifest)
    if glyph_id not in manifest["glyphs"]:
        raise ValidationError("Cannot edit an unknown glyph ID.")
    _pixels(pixels, manifest["width"], manifest["height"])
    revised = deepcopy(manifest)
    revised["glyphs"][glyph_id] = pixels[:]
    revised["version"] += 1
    revised["previous_digest"] = manifest["digest"]
    revised["digest"] = alphabet_digest(revised)
    return validate_alphabet(revised)


def reorder_alphabet(manifest: dict, symbols: list[str]) -> dict:
    validate_alphabet(manifest)
    if not isinstance(symbols, list) or len(symbols) != len(manifest["symbols"]) or set(symbols) != set(manifest["symbols"]):
        raise ValidationError("Reordering must contain every existing glyph ID exactly once.")
    revised = deepcopy(manifest)
    revised["symbols"] = symbols[:]
    revised["version"] += 1
    revised["previous_digest"] = manifest["digest"]
    revised["digest"] = alphabet_digest(revised)
    return validate_alphabet(revised)


def glyph_stream(symbols: list[str], alphabet: dict) -> dict:
    validate_alphabet(alphabet)
    codec = GlyphRankCodec(alphabet["symbols"])
    rank = codec.encode_verified(symbols)
    return {"schema": "pandr-glyph-stream/1", "alphabet_id": alphabet["id"],
            "alphabet_version": alphabet["version"], "alphabet_digest": alphabet["digest"],
            "codec_id": codec.codec_id, "symbols": list(symbols), "symbol_count": len(symbols),
            "sequential_id": rank, "reverse_verified": True}


def glyphs_to_text(symbols: list[str], export_map: dict[str, str], *, framed: bool = False):
    """Export explicitly mapped glyphs. Multi-character tokens require framing."""
    if not isinstance(export_map, dict) or not isinstance(symbols, list):
        raise ValidationError("Export requires glyph IDs and an explicit export map.")
    values = list(export_map.values())
    if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
        raise ValidationError("Export tokens must be nonempty, unique strings.")
    if not framed and any(len(value) != 1 for value in values):
        raise ValidationError("Multi-character export tokens require framed=True.")
    try:
        output = [export_map[symbol] for symbol in symbols]
    except (KeyError, TypeError) as exc:
        raise ValidationError("A glyph lacks an explicit export mapping.") from exc
    return output if framed else "".join(output)


def text_to_glyphs(text: str | list[str], export_map: dict[str, str]) -> list[str]:
    if not isinstance(export_map, dict):
        raise ValidationError("Text import requires an explicit glyph-to-text export map.")
    glyphs_to_text([], export_map, framed=isinstance(text, list))
    if not isinstance(text, (str, list)):
        raise ValidationError("Import text must be a string or a framed token list.")
    reverse = {value: glyph_id for glyph_id, value in export_map.items()}
    try:
        return [reverse[token] for token in text]
    except (KeyError, TypeError) as exc:
        raise ValidationError("Input contains a token outside the declared export map.") from exc
