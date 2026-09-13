"""Read exported formats back and verify exact pixels, samples and timelines."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from PIL import Image

from pandr.alphabets import (create_alphabet, define_alphabet, edit_glyph,
                             validate_alphabet, reorder_alphabet, glyph_stream,
                             glyphs_to_text, text_to_glyphs)
from pandr.artifacts import render_artifact, render_glyph_image
from pandr.errors import ValidationError, BudgetExceeded


class AlphabetTests(unittest.TestCase):
    def test_original_enumeration_and_distinct_bitmaps(self):
        alphabet = create_alphabet(count=95)
        self.assertEqual(create_alphabet(count=95), alphabet)
        self.assertEqual(len({tuple(rows) for rows in alphabet["glyphs"].values()}), 95)
        self.assertEqual(alphabet["glyphs"]["g0000"][0], "0111111111")
        self.assertEqual(alphabet["glyphs"]["g0001"][1], "1100000001")
        self.assertEqual(alphabet["glyphs"]["g0002"][1], "1010000001")
        with self.assertRaises(ValidationError):
            create_alphabet(count=3, width=3, height=3)
        with self.assertRaises(ValidationError):
            create_alphabet(count=True)

    def test_edit_is_new_version_and_rejects_duplicate(self):
        original = create_alphabet(count=2, width=4, height=4)
        edited = edit_glyph(original, "g0000", ["1111", "1111", "1111", "1111"])
        self.assertEqual(original["version"], 1)
        self.assertEqual(edited["version"], 2)
        self.assertEqual(edited["previous_digest"], original["digest"])
        self.assertNotEqual(edited["digest"], original["digest"])
        with self.assertRaises(ValidationError):
            edit_glyph(original, "g0000", original["glyphs"]["g0001"])
        tampered = deepcopy(original)
        tampered["glyphs"]["g0000"][0] = "1111"
        with self.assertRaises(ValidationError):
            validate_alphabet(tampered)
        reordered = reorder_alphabet(original, list(reversed(original["symbols"])))
        self.assertEqual(reordered["version"], 2)

    def test_explicit_definition_maps_and_glyph_stream(self):
        alphabet = define_alphabet("tiny", {"seed": ["0"], "full": ["1"]}, width=1, height=1)
        stream = glyph_stream(["seed", "full"], alphabet)
        self.assertEqual(stream["sequential_id"], "4")
        mapping = {"seed": "A", "full": "\n"}
        self.assertEqual(glyphs_to_text(["seed", "full", "seed"], mapping), "A\nA")
        self.assertEqual(text_to_glyphs("A\nA", mapping), ["seed", "full", "seed"])
        tokens = {"seed": "one", "full": "one-two"}
        with self.assertRaises(ValidationError):
            glyphs_to_text(["seed"], tokens)
        framed = glyphs_to_text(["full", "seed"], tokens, framed=True)
        self.assertEqual(text_to_glyphs(framed, tokens), ["full", "seed"])


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.alphabet = create_alphabet(count=95)

    def tearDown(self):
        self.temporary.cleanup()

    def render(self, kind, payload, parameters=None):
        result = render_artifact(kind, payload, parameters or {}, self.directory, self.alphabet)
        data = (self.directory / result["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["sha256"])
        self.assertEqual(len(data), result["bytes"])
        for dependency in result["dependencies"]:
            dependent = (self.directory / dependency["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(dependent).hexdigest(), dependency["sha256"])
            self.assertEqual(len(dependent), dependency["bytes"])
        return result

    def test_text_and_code_are_exact_utf8_and_never_execute(self):
        text = " P&R\r\nΔ\t🙂 "
        result = self.render("text", text)
        self.assertEqual((self.directory / result["path"]).read_bytes(), text.encode("utf-8"))
        source = "raise RuntimeError('generated code must not execute')\n"
        code = self.render("code", source)
        self.assertEqual((self.directory / code["path"]).read_bytes(), source.encode("utf-8"))
        self.assertEqual(code["validation"], "unvalidated")
        self.assertFalse(code["executed"])
        self.assertEqual(code, self.render("code", source))

    def test_png_decodes_exact_glyph_pixels_and_stream(self):
        symbols = ["g0000", "g0001", "g0002"]
        parameters = {"scale": 2, "gap": 1, "padding": 0}
        result = self.render("image", symbols, parameters)
        with Image.open(self.directory / result["path"]) as picture:
            self.assertEqual(picture.size, (62, 20))
            self.assertEqual(picture.getpixel((0, 0)), (255, 255, 255, 255))
            self.assertEqual(picture.getpixel((2, 0)), (28, 64, 216, 255))
            self.assertEqual(picture.tobytes(), render_glyph_image(symbols, self.alphabet, parameters).tobytes())
        stream = json.loads((self.directory / result["glyph_stream"]).read_text(encoding="utf-8"))
        self.assertEqual(stream["symbols"], symbols)
        self.assertTrue(stream["reverse_verified"])

    def test_direct_pixels_and_explicit_text_mapping(self):
        payload = {"width": 2, "height": 1, "pixels": [[[1, 2, 3, 4], [254, 253, 252, 251]]]}
        result = self.render("image", payload)
        with Image.open(self.directory / result["path"]) as picture:
            self.assertEqual([picture.getpixel((x, 0)) for x in range(2)], [(1, 2, 3, 4), (254, 253, 252, 251)])
        with self.assertRaises(ValidationError):
            self.render("image", "A")
        mapped = self.render("image", "A", {"export_map": {"g0001": "A"}})
        self.assertIn("glyph_stream", mapped)

    def test_pcm_wav_samples_channels_and_integer_square_wave(self):
        samples = [-32768, 32767, 0, -1, 1, 100]
        pcm = self.render("audio", samples, {"mode": "pcm", "channels": 2, "sample_rate": 8000})
        with wave.open(str(self.directory / pcm["path"]), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getnframes()), (2, 8000, 2, 3))
            self.assertEqual(wav.readframes(3), b"".join(struct.pack("<h", sample) for sample in samples))
        synth = self.render("audio", [1, 2], {"sample_rate": 8000, "samples_per_event": 40, "base_frequency": 1000, "frequency_step": 0, "amplitude": 10})
        with wave.open(str(self.directory / synth["path"]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 80)
            self.assertEqual(struct.unpack("<8h", wav.readframes(8)), (10, 10, 10, 10, -10, -10, -10, -10))
        self.assertEqual(synth["duration"], "1/100")
        self.assertEqual(synth, self.render("audio", [1, 2], synth["parameters"]))

    def test_apng_plays_repeated_frames_in_exact_order(self):
        result = self.render("video", [1, 2, 2, 3], {"fps": "30000/1001", "scale": 1})
        self.assertEqual(result["mime"], "image/apng")
        self.assertEqual(result["frame_count"], result["encoded_frame_count"])
        timeline = json.loads((self.directory / result["timeline"]).read_text(encoding="utf-8"))
        self.assertEqual(timeline["duration"], "1001/7500")
        with Image.open(self.directory / result["path"]) as animation:
            self.assertTrue(animation.is_animated)
            self.assertEqual(animation.n_frames, 4)
            for index, frame in enumerate(timeline["frames"]):
                animation.seek(index)
                self.assertEqual(hashlib.sha256(animation.convert("RGBA").tobytes()).hexdigest(), frame["pixel_sha256"])
                self.assertAlmostEqual(animation.info["duration"], float(Fraction(1001, 30)))
                self.assertEqual(Fraction(frame["timestamp"]), Fraction(index * 1001, 30000))
        self.assertEqual(result, self.render("video", [1, 2, 2, 3], result["parameters"]))

    def test_video_optional_audio_has_exact_shared_duration(self):
        parameters = {"fps": 8, "audio": [1, 2], "audio_parameters": {"sample_rate": 8000, "samples_per_event": 1000}}
        result = self.render("video", [1, 2], parameters)
        timeline = json.loads((self.directory / result["timeline"]).read_text(encoding="utf-8"))
        self.assertEqual(timeline["audio"]["duration"], timeline["duration"])
        self.assertFalse(timeline["audio_muxed"])
        with wave.open(str(self.directory / timeline["audio"]["path"]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 2000)

    def test_budgets_and_paths_reject_before_writing(self):
        with self.assertRaises(BudgetExceeded):
            self.render("text", "too long", {"max_output_bytes": 1})
        self.assertEqual(list(self.directory.iterdir()), [])
        with self.assertRaises(ValidationError):
            self.render("code", "pass", {"filename": "../escaped.py"})
        with self.assertRaises(BudgetExceeded):
            self.render("image", {"width": 4096, "height": 4096, "commands": []})
        with self.assertRaises(BudgetExceeded):
            self.render("video", [1] * 241)
        with self.assertRaises(ValidationError):
            self.render("audio", [40000], {"mode": "pcm"})
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
