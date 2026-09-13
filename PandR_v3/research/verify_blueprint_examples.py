"""Verify blueprint examples and saved provenance; this is not the P&R runtime.

Run from any working directory. Writes blueprint-verification.json beside itself.
Only reads architectural sources and saved evidence; never executes stored cells.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

from reference_codec import ShortlexCodec


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def affine(x: int, y: int, a: int = 1, b: int = 0):
    return Fraction(y - b, a), Fraction(-b, a) - x


def rank_bytes(data: bytes) -> int:
    result = 0
    for value in data:
        result = result * 256 + value + 1
    return result


def unrank_bytes(rank: int) -> bytes:
    values = []
    while rank:
        rank, value = divmod(rank - 1, 256)
        values.append(value)
    return bytes(reversed(values))


def main() -> None:
    directory = Path(__file__).resolve().parent
    workspace = directory.parent.parent
    points = [("p0", 2, 17), ("p1", 3, 7), ("p2", 5, 3),
              ("p3", 7, 23), ("p4", 37, 11)]
    terms = [("v", 17), ("v", 7), ("v", 3), ("v", 23), ("h", 37), ("v", 23)]
    events = []
    for occurrence, (channel, magnitude) in enumerate(terms):
        for point_id, x, y in points:
            displacement = affine(x, y)[channel == "h"]
            if abs(displacement) == magnitude:
                events.append({"occurrence": occurrence, "point": point_id,
                               "channel": channel, "magnitude": str(magnitude),
                               "direction": "right" if displacement > 0 else "left"})
    assert [event["point"] for event in events] == ["p0", "p1", "p2", "p3", "p4", "p3"]
    assert [event["direction"] for event in events] == ["right"] * 4 + ["left", "right"]
    assert affine(3, 7, b=5) == (Fraction(2), Fraction(-8))
    assert affine(3, 7, a=2, b=1) == (Fraction(3), Fraction(-7, 2))
    # Translation of f=x by dx=2,dy=5 gives x+3; g=2*x follows it.
    for value in (Fraction(-7), Fraction(0), Fraction(3, 2)):
        assert 2 * ((value - 2) + 5) == 2 * value + 6
        assert (2 * value) + 3 != 2 * (value + 3)
    codec = ShortlexCodec()
    text = "".join(codec.decode(event["magnitude"]) for event in events)
    assert text == '0&"6D6'
    canonical_events = json.dumps(events, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert unrank_bytes(rank_bytes(canonical_events)) == canonical_events
    for data in (b"", b"\x00", b"\x00\x00", b"\x00\xff", bytes(range(256))):
        assert unrank_bytes(rank_bytes(data)) == data
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    for source in provenance["sources"]:
        assert sha256(directory / source["local_file"]) == source["sha256"], source["local_file"]
    original_image = workspace / "2d-llm.png"
    copied_image = directory / "2d-llm-reference.png"
    assert sha256(original_image) == sha256(copied_image)
    source_names = ["operating-system.py", "matrix_controller.py", "test_operating_system.py",
                    "cosma-x1.cpp", "cosma-state-revision-99.json", "operating-system-config.json",
                    "operating-system-state.json", "matrix-controller-walkthrough.md",
                    "operating-system-readme.md", "demo.py"]
    inventory = []
    for name in source_names:
        source = workspace / "operating-system" / name
        inventory.append({"path": str(source), "bytes": source.stat().st_size,
                          "lines": len(source.read_text(encoding="utf-8").splitlines()),
                          "sha256": sha256(source)})
    blueprint = directory.parent / "2d-llm-blueprint.md"
    report = {"schema": "pandr-blueprint-verification-v1", "status": "passed",
              "scope": "Exact affine/composition examples, six-term fixture, ASCII rank projection, "
                       "bijective byte framing, saved-source hashes, unchanged diagram copy. "
                       "This does not implement or test the proposed P&R application.",
              "blueprint_sha256": sha256(blueprint), "sequence_events": events,
              "ascii_projection": text, "web_source_hashes_verified": len(provenance["sources"]),
              "reference_image_sha256": sha256(original_image), "local_source_inventory": inventory}
    (directory / "blueprint-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "sequence_occurrences": len(events),
                      "ascii_projection": text, "web_source_hashes": len(provenance["sources"]),
                      "architectural_sources": len(inventory)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
