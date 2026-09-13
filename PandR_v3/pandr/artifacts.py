"""Bounded deterministic text, PNG, PCM WAV, APNG and source-code exporters.

All renderers create real formats. APNG is a silent playable video baseline;
an optional WAV track is retained in the exact rational timeline as a sidecar.
Generated code is data and is never executed by this module.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import wave
import zlib

from PIL import Image, ImageDraw, __version__ as PILLOW_VERSION

from .alphabets import validate_alphabet, text_to_glyphs, glyph_stream
from .codecs import GlyphRankCodec, decimal_string
from .errors import ValidationError, BudgetExceeded

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_CANVAS_PIXELS = 1_048_576
MAX_FRAMES = 240
MAX_FRAME_PIXELS = 16_777_216
MAX_AUDIO_SAMPLES = 2_000_000


def _int(value, label, minimum=0, maximum=2_147_483_647):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{label} must be an integer in {minimum}..{maximum}.")
    return value


def _json_bytes(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValidationError("Artifact data and parameters must be finite JSON data.") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _color(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        raise ValidationError("Colors must be RGB or RGBA lists.")
    result = tuple(_int(channel, "color channel", maximum=255) for channel in value)
    return result if len(result) == 4 else result + (255,)


def _canvas(width, height, background):
    _int(width, "width", 1, 4096)
    _int(height, "height", 1, 4096)
    if width * height > MAX_CANVAS_PIXELS:
        raise BudgetExceeded("Canvas exceeds the one-million-pixel budget.")
    return Image.new("RGBA", (width, height), _color(background))


def _symbol_ids(payload, alphabet, parameters):
    if isinstance(payload, str):
        mapping = parameters.get("export_map")
        if mapping is None:
            raise ValidationError("Rendering ordinary text as original glyphs requires an explicit export_map.")
        return text_to_glyphs(payload, mapping)
    if isinstance(payload, dict):
        if "symbols" in payload:
            payload = payload["symbols"]
        elif "ranks" in payload:
            result = []
            codec = GlyphRankCodec(alphabet["symbols"])
            for rank in payload["ranks"]:
                rank = decimal_string(rank) if type(rank) is int else rank
                result.extend(codec.decode(rank))
                if len(result) > 4096:
                    raise BudgetExceeded("Decoded glyph stream exceeds 4096 symbols.")
            return result
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise ValidationError("Glyph image payload must be a list of glyph IDs or a typed ranks object.")
    GlyphRankCodec(alphabet["symbols"]).encode(payload)
    return payload


def render_glyph_image(payload, alphabet: dict, parameters: dict | None = None) -> Image.Image:
    """Return an RGBA image using exact integer nearest-neighbor pixel placement."""
    validate_alphabet(alphabet)
    params = parameters or {}
    symbols = _symbol_ids(payload, alphabet, params)
    scale = _int(params.get("scale", 4), "scale", 1, 32)
    gap = _int(params.get("gap", 2), "gap", 0, 128)
    padding = _int(params.get("padding", 8), "padding", 0, 512)
    columns = _int(params.get("columns", min(16, max(1, len(symbols)))), "columns", 1, 4096)
    columns = min(columns, max(1, len(symbols)))
    rows = max(1, (len(symbols) + columns - 1) // columns)
    gw, gh = alphabet["width"] * scale, alphabet["height"] * scale
    default_width = padding * 2 + columns * gw + (columns - 1) * gap
    default_height = padding * 2 + rows * gh + (rows - 1) * gap
    width, height = params.get("width", default_width), params.get("height", default_height)
    _int(width, "width", 1, 4096)
    _int(height, "height", 1, 4096)
    if width < default_width or height < default_height:
        raise ValidationError("Requested canvas would clip the declared glyph layout.")
    background = _color(params.get("background", [255, 255, 255, 255]))
    foreground = _color(params.get("foreground", [28, 64, 216, 255]))
    output = _canvas(width, height, background)
    draw = ImageDraw.Draw(output)
    for position, symbol in enumerate(symbols):
        left = padding + (position % columns) * (gw + gap)
        top = padding + (position // columns) * (gh + gap)
        for y, row in enumerate(alphabet["glyphs"][symbol]):
            for x, bit in enumerate(row):
                if bit == "1":
                    draw.rectangle((left + x * scale, top + y * scale,
                                    left + (x + 1) * scale - 1, top + (y + 1) * scale - 1), fill=foreground)
    return output


def render_pixel_image(payload: dict, parameters: dict | None = None) -> Image.Image:
    """Render explicit RGBA pixels or point/rectangle/line pixel commands."""
    params = parameters or {}
    width = payload.get("width", params.get("width", 256))
    height = payload.get("height", params.get("height", 256))
    output = _canvas(width, height, payload.get("background", params.get("background", [255, 255, 255, 255])))
    if "pixels" in payload:
        rows = payload["pixels"]
        if not isinstance(rows, list) or len(rows) != height or any(not isinstance(row, list) or len(row) != width for row in rows):
            raise ValidationError("Pixel payload must contain exactly height rows of width colors.")
        output.putdata([_color(pixel) for row in rows for pixel in row])
    commands = payload.get("commands", [])
    if not isinstance(commands, list) or len(commands) > 65536:
        raise BudgetExceeded("Pixel command list exceeds 65536 commands.")
    draw = ImageDraw.Draw(output)
    for command in commands:
        if not isinstance(command, dict):
            raise ValidationError("Pixel commands must be objects.")
        operation = command.get("op")
        color = _color(command.get("color", [28, 64, 216, 255]))
        if operation == "point":
            x = _int(command.get("x"), "x", 0, width - 1)
            y = _int(command.get("y"), "y", 0, height - 1)
            draw.point((x, y), fill=color)
        elif operation in ("rectangle", "line"):
            box = command.get("box", command.get("points"))
            if not isinstance(box, list) or len(box) != 4:
                raise ValidationError("Rectangle/line requires four integer coordinates.")
            coords = tuple(_int(n, "coordinate", 0, width - 1 if i % 2 == 0 else height - 1) for i, n in enumerate(box))
            if operation == "rectangle":
                if coords[2] < coords[0] or coords[3] < coords[1]:
                    raise ValidationError("Rectangle bounds must be ordered.")
                draw.rectangle(coords, fill=color)
            else:
                draw.line(coords, fill=color, width=_int(command.get("width", 1), "line width", 1, 128))
        else:
            raise ValidationError(f"Unknown pixel command {operation!r}.")
    return output


def _image(payload, params, alphabet):
    if isinstance(payload, dict) and any(key in payload for key in ("pixels", "commands")):
        return render_pixel_image(payload, params)
    if alphabet is None:
        raise ValidationError("Glyph image generation requires an alphabet manifest.")
    return render_glyph_image(payload, alphabet, params)


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def _apng(frames, delay: Fraction, loop: int):
    """Encode full RGBA frames with exact APNG timing and no frame coalescing."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    width, height = frames[0].size
    chunks = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
              chunk(b"acTL", struct.pack(">II", len(frames), loop))]
    sequence = 0
    for index, frame in enumerate(frames):
        control = struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, delay.numerator, delay.denominator, 0, 0)
        chunks.append(chunk(b"fcTL", control))
        sequence += 1
        pixels = frame.tobytes()
        row_length = width * 4
        filtered = b"".join(b"\0" + pixels[start:start + row_length] for start in range(0, len(pixels), row_length))
        compressed = zlib.compress(filtered, 9)
        if index == 0:
            chunks.append(chunk(b"IDAT", compressed))
        else:
            chunks.append(chunk(b"fdAT", struct.pack(">I", sequence) + compressed))
            sequence += 1
    chunks.append(chunk(b"IEND", b""))
    return b"".join(chunks)


def _audio(payload, params):
    if not isinstance(payload, list):
        raise ValidationError("Audio payload must be a list of integer ranks or PCM samples.")
    if len(payload) > MAX_AUDIO_SAMPLES:
        raise BudgetExceeded("Audio input exceeds the sample budget.")
    mode = params.get("mode", "square")
    rate = _int(params.get("sample_rate", 16000), "sample_rate", 8000, 192000)
    channels = _int(params.get("channels", 1), "channels", 1, 2)
    if params.get("bit_depth", 16) != 16:
        raise ValidationError("The deterministic WAV baseline supports signed 16-bit PCM.")
    if mode == "pcm":
        samples = [_int(sample, "PCM sample", -32768, 32767) for sample in payload]
        if len(samples) % channels:
            raise ValidationError("Interleaved PCM samples must be divisible by the channel count.")
        timings = []
        frame_count = len(samples) // channels
    elif mode in ("square", "events"):
        per_event = _int(params.get("samples_per_event", rate // 8), "samples_per_event", 1, MAX_AUDIO_SAMPLES)
        amplitude = _int(params.get("amplitude", 8000), "amplitude", 0, 32767)
        base_frequency = _int(params.get("base_frequency", 110), "base_frequency", 1, rate // 2 - 1)
        step = _int(params.get("frequency_step", 7), "frequency_step", 0, rate // 2)
        if len(payload) * per_event * channels > MAX_AUDIO_SAMPLES:
            raise BudgetExceeded("Synthesized audio exceeds the total sample budget.")
        samples, timings = [], []
        for event, rank in enumerate(payload):
            rank = _int(rank, "audio rank", 0, 1_000_000_000)
            frequency = base_frequency + rank * step
            if frequency >= rate // 2:
                raise ValidationError("Rank-derived frequency reaches Nyquist; declare a smaller frequency_step.")
            timings.append({"event": event, "rank": rank, "start_sample": event * per_event,
                            "sample_count": per_event, "frequency": frequency})
            for sample in range(per_event):
                value = amplitude if ((sample * frequency * 2) // rate) % 2 == 0 else -amplitude
                samples.extend([value] * channels)
        frame_count = len(payload) * per_event
    else:
        raise ValidationError("Audio mode must be pcm, square or events.")
    pcm = b"".join(struct.pack("<h", sample) for sample in samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue(), {"renderer": "pandr-integer-pcm-v1", "mode": mode,
                              "sample_rate": rate, "channels": channels, "bit_depth": 16,
                              "sample_frames": frame_count, "sample_values": len(samples),
                              "duration": str(Fraction(frame_count, rate)), "pcm_sha256": _sha(pcm),
                              "event_timing": timings}


def _video(payload, params, alphabet):
    if not isinstance(payload, list) or not payload:
        raise ValidationError("Video payload must contain at least one frame or rank.")
    if len(payload) > MAX_FRAMES:
        raise BudgetExceeded("Video exceeds the 240-frame budget.")
    try:
        fps_value = params.get("fps", "8")
        if isinstance(fps_value, (float, bool)):
            raise ValueError("use rational frame rate")
        fps = Fraction(fps_value)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValidationError("fps must be an exact positive rational string or integer.") from exc
    if not 1 <= fps <= 120:
        raise ValidationError("Frame rate must be in 1..120 frames per second.")
    # APNG's delay numerator and denominator are unsigned 16-bit integers.
    delay = 1 / fps
    if delay.numerator > 65535 or delay.denominator > 65535:
        raise ValidationError("Frame rate cannot be represented exactly by APNG timing.")
    frames, descriptors = [], []
    source_pixels = 0
    image_params = {key: value for key, value in params.items() if key not in ("width", "height")}
    for index, frame in enumerate(payload):
        if type(frame) is int or isinstance(frame, str) and re.fullmatch(r"[0-9]+", frame):
            if alphabet is None:
                raise ValidationError("Rank video frames require an alphabet manifest.")
            rank = decimal_string(frame) if type(frame) is int else frame
            content = {"ranks": [rank]}
        elif isinstance(frame, dict) and "payload" in frame:
            content = frame["payload"]
        else:
            content = frame
        frame_params = dict(image_params)
        if isinstance(frame, dict) and "parameters" in frame:
            if not isinstance(frame["parameters"], dict):
                raise ValidationError("Per-frame parameters must be an object.")
            frame_params.update(frame["parameters"])
        picture = _image(content, frame_params, alphabet)
        source_pixels += picture.width * picture.height
        if source_pixels > MAX_FRAME_PIXELS:
            raise BudgetExceeded("Video exceeds the total frame-pixel budget.")
        frames.append(picture)
        descriptors.append({"index": index, "timestamp": str(Fraction(index, 1) / fps),
                            "duration": str(delay)})
    width = params.get("width", max(frame.width for frame in frames))
    height = params.get("height", max(frame.height for frame in frames))
    if type(width) is not int or type(height) is not int or width * height * len(frames) > MAX_FRAME_PIXELS:
        raise BudgetExceeded("Video exceeds the total frame-pixel budget.")
    normalized = []
    for frame in frames:
        if frame.width > width or frame.height > height:
            raise ValidationError("Video dimensions would clip a source frame.")
        canvas = _canvas(width, height, params.get("background", [255, 255, 255, 255]))
        canvas.paste(frame, (0, 0))
        normalized.append(canvas)
    data = _apng(normalized, delay, _int(params.get("loop", 0), "loop", 0, 65535))
    with Image.open(io.BytesIO(data)) as decoded:
        encoded_count = decoded.n_frames
    sidecars = []
    for frame, descriptor in zip(normalized, descriptors):
        frame_data = _png(frame)
        name = f"frame-{_sha(frame_data)}.png"
        descriptor.update({"path": name, "sha256": _sha(frame_data), "bytes": len(frame_data),
                           "pixel_sha256": _sha(frame.tobytes())})
        sidecars.append((name, frame_data))
    timeline = {"schema": "pandr-video-timeline/1", "fps": str(fps), "width": width, "height": height,
                "frames": descriptors, "frame_count": len(frames), "duration": str(len(frames) / fps),
                "audio_muxed": False}
    if "audio" in params:
        audio_data, audio_details = _audio(params["audio"], params.get("audio_parameters", {}))
        audio_name = f"audio-{_sha(audio_data)}.wav"
        if Fraction(audio_details["duration"]) != len(frames) / fps:
            raise ValidationError("Optional audio must exactly match the rational video duration.")
        timeline["audio"] = {"path": audio_name, "sha256": _sha(audio_data), "bytes": len(audio_data), **audio_details}
        sidecars.append((audio_name, audio_data))
    timeline_data = _json_bytes(timeline)
    timeline_name = f"timeline-{_sha(timeline_data)}.json"
    sidecars.append((timeline_name, timeline_data))
    return data, {"renderer": "pandr-apng-v1", "pillow_version": PILLOW_VERSION, "zlib_version": zlib.ZLIB_VERSION,
                  "width": width, "height": height, "fps": str(fps), "frame_count": len(frames),
                  "encoded_frame_count": encoded_count, "duration": timeline["duration"],
                  "audio_muxed": False, "timeline": timeline_name,
                  "frame_pixel_hashes": [item["pixel_sha256"] for item in descriptors]}, sidecars


def render_artifact(kind: str, payload, parameters: dict | None, destination: Path,
                    alphabet: dict | None = None) -> dict:
    """Render into a staging directory and return relative, content-addressed paths.

    The caller owns transactional publication of the directory. Every dependency
    is included in ``dependencies``; the sum of primary and sidecar bytes is
    bounded before any file is written. Existing names with different bytes fail.
    """
    if not isinstance(parameters, (dict, type(None))):
        raise ValidationError("Artifact parameters must be an object.")
    params = deepcopy(parameters or {})
    _json_bytes(params)
    budget = _int(params.get("max_output_bytes", MAX_OUTPUT_BYTES), "max_output_bytes", 1, MAX_OUTPUT_BYTES)
    details, sidecars = {}, []
    if kind in ("text", "code"):
        if not isinstance(payload, str):
            raise ValidationError("Text and code artifacts require an explicit string payload.")
        if len(payload) > budget:
            raise BudgetExceeded("Text character count already exceeds the output byte budget.")
        try:
            data = payload.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValidationError("Text contains an invalid Unicode surrogate.") from exc
        details = {"renderer": "pandr-utf8-v1", "encoding": "utf-8", "normalization": "none"}
        if kind == "text":
            extension, mime = ".txt", "text/plain; charset=utf-8"
        else:
            language = params.get("language", "python")
            extensions = {"python": ".py", "javascript": ".js", "typescript": ".ts", "json": ".json",
                          "cpp": ".cpp", "c": ".c", "html": ".html", "css": ".css", "text": ".txt"}
            if language not in extensions:
                raise ValidationError("Unsupported source language; use language=text for an opaque source file.")
            extension, mime = extensions[language], "text/plain; charset=utf-8"
            details.update({"language": language, "validation": "unvalidated", "executed": False})
    elif kind == "image":
        picture = _image(payload, params, alphabet)
        data, extension, mime = _png(picture), ".png", "image/png"
        details = {"renderer": "pandr-pixels-v1", "pillow_version": PILLOW_VERSION,
                   "width": picture.width, "height": picture.height, "mode": picture.mode,
                   "compositing": "replace-rgba", "scan_order": "row-major",
                   "pixel_sha256": _sha(picture.tobytes())}
        if alphabet is not None and (not isinstance(payload, dict) or any(key in payload for key in ("symbols", "ranks"))):
            stream = glyph_stream(_symbol_ids(payload, alphabet, params), alphabet)
            stream_data = _json_bytes(stream)
            stream_name = f"glyphs-{_sha(stream_data)}.json"
            sidecars.append((stream_name, stream_data))
            details["glyph_stream"] = stream_name
    elif kind == "audio":
        data, details = _audio(payload, params)
        extension, mime = ".wav", "audio/wav"
    elif kind == "video":
        data, details, sidecars = _video(payload, params, alphabet)
        extension, mime = ".png", "image/apng"
    else:
        raise ValidationError("Artifact kind must be text, image, audio, video or code.")
    unique_sidecars = dict(sidecars)
    total_bytes = len(data) + sum(len(value) for value in unique_sidecars.values())
    if total_bytes > budget:
        raise BudgetExceeded(f"Artifact and dependencies require {total_bytes} bytes; budget is {budget}.")
    digest = _sha(data)
    filename = f"{kind}-{digest}{extension}"
    requested = params.get("filename")
    if requested is not None:
        if not isinstance(requested, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", requested) or Path(requested).suffix.lower() != extension:
            raise ValidationError("filename must be a safe simple name with the renderer's extension.")
        filename = requested
    files = {**unique_sidecars, filename: data}
    if len(files) != len(unique_sidecars) + 1:
        raise ValidationError("Artifact filename collides with a dependency.")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = destination / name
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise ValidationError("An existing artifact filename has different content.")
        else:
            with target.open("xb") as handle:
                handle.write(content)
    manifest = {"schema": "pandr-artifact/1", "kind": kind, "path": filename, "mime": mime,
                "sha256": digest, "bytes": len(data), "total_bytes": total_bytes, "parameters": params,
                "dependencies": [{"path": name, "sha256": _sha(content), "bytes": len(content)}
                                 for name, content in sorted(unique_sidecars.items())], **details}
    if alphabet is not None:
        manifest.update({"alphabet_id": alphabet["id"], "alphabet_version": alphabet["version"],
                         "alphabet_digest": alphabet["digest"]})
    return manifest
