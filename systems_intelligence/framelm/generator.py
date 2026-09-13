"""Unified ordinal generator derived from the supplied 4.cpp, 5.cpp, 6.cpp."""
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re
import string
from .controller.hooks import observed, emit

U64_MAX = (1 << 64) - 1
ALPHABETS = {
    "original": string.ascii_lowercase + string.ascii_uppercase + string.digits + " \n",
    "safe": string.ascii_lowercase + string.ascii_uppercase + string.digits + "_",
    "digits": string.digits, "lower": string.ascii_lowercase,
}
TEMPLATE_KEYS = {"value", "payload", "ordinal", "handle", "object_name", "input_name", "flow_name", "output_name"}


def fnv1a64(value: str) -> str:
    result = 14695981039346656037
    for byte in value.encode("utf-8"):
        result = ((result ^ byte) * 1099511628211) & U64_MAX
    return f"{result:016x}"


def escaped_field(value: str) -> str:
    out = []
    for c in value:
        out.append({"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(
            c, f"\\x{ord(c):02X}" if ord(c) < 32 or ord(c) == 127 else c))
    return "".join(out)


def render(template, values):
    # Only literal named fields: no attribute traversal, conversion or format code.
    for _, key, spec, conversion in string.Formatter().parse(template):
        if key is not None and (key not in TEMPLATE_KEYS or spec or conversion):
            raise ValueError(f"Unsupported template field: {key}. Use simple named fields only.")
    return template.format_map(values)


@dataclass(frozen=True)
class Recipe:
    mode: str = "literal"
    object_name: str = "context"
    input_name: str = "input"
    flow_name: str = "resolve"
    output_name: str = "FrameLM contexts"
    input_value: str = "A context supplies reference information for a prompt."
    alphabet: str = "safe"
    width: int = 4
    start: int = 0
    limit: int = 100
    all: bool = False
    allow_large: bool = False
    handle_width: int = 7
    prefix: str = ""
    suffix: str = ""
    separator: str = "\n"
    title_template: str = "{object_name} / {handle}"
    body_template: str = "{value}"
    tags: str = "generated"
    enabled: bool = True

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Recipe must be a JSON object.")
        unknown = set(value) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown recipe fields: {sorted(unknown)}")
        value = dict(value)
        for key in ("width", "start", "limit", "handle_width"):
            if isinstance(value.get(key), str):
                if not re.fullmatch(r"[0-9]{1,20}", value[key]):
                    raise ValueError(f"{key} must be an unsigned 64-bit integer.")
                value[key] = int(value[key])
        recipe = cls(**value)
        recipe.plan()
        return recipe

    def symbols(self):
        if self.mode == "numeric":
            return ALPHABETS["digits"]
        if self.mode == "symbols":
            if self.alphabet not in ALPHABETS:
                raise ValueError("Unknown alphabet preset.")
            return ALPHABETS[self.alphabet]
        return self.input_value

    @observed('generator.plan')
    def plan(self):
        if self.mode not in {"symbols", "numeric", "cartesian", "literal", "repeat", "reverse"}:
            raise ValueError("Unknown generation mode.")
        for name in ("object_name", "input_name", "flow_name", "output_name", "input_value", "alphabet", "prefix", "suffix", "separator", "title_template", "body_template", "tags"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
                raise ValueError(f"{name} must be text of at most 4096 UTF-8 bytes.")
        for name in ("object_name", "input_name", "flow_name", "output_name"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be empty.")
        for name in ("width", "handle_width", "start", "limit"):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= U64_MAX:
                raise ValueError(f"{name} must be an unsigned 64-bit integer.")
        for name in ("enabled", "all", "allow_large"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean.")
        max_width = {"symbols": 16, "numeric": 18, "cartesian": 64, "repeat": 1000000}.get(self.mode, U64_MAX)
        if not 1 <= self.width <= max_width or not 1 <= self.handle_width <= 20:
            raise ValueError(f"Width must be 1–{max_width}; handle width must be 1–20.")
        if self.mode in {"symbols", "numeric", "cartesian"}:
            alphabet = self.symbols()
            if not alphabet:
                raise ValueError("Cartesian alphabet cannot be empty.")
            total = len(alphabet) ** self.width
            if total > U64_MAX:
                raise ValueError("Combination count exceeds uint64, matching the C++ overflow guard.")
        else:
            total = self.width if self.mode == "repeat" else 1
        remaining = max(0, total - self.start)
        count = remaining if self.all else min(remaining, self.limit)
        if count > 100000 and not self.allow_large:
            raise ValueError("Select Allow large batch for more than 100,000 records.")
        # A hard per-job bound keeps this desktop application usable. Large spaces
        # remain accessible through consecutive start/limit windows.
        if count > 1000000:
            raise ValueError("Maximum one million records per batch. Use start/limit windows.")
        sample = dict.fromkeys(TEMPLATE_KEYS, "sample")
        for text in (self.title_template, self.body_template):
            if not render(text, sample).strip():
                raise ValueError("Title and body templates must produce nonempty text.")
        return {"total": str(total), "count": count, "start": str(self.start),
                "next_start": str(self.start + count), "truncated": self.start + count < total}

    def signature(self):
        data = asdict(self)
        for key in ("start", "limit", "all", "allow_large", "enabled"):
            data.pop(key)
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def payload(self, ordinal):
        if self.mode in {"symbols", "numeric", "cartesian"}:
            alphabet = self.symbols()
            value = [alphabet[0]] * self.width
            for position in range(self.width - 1, -1, -1):
                ordinal, digit = divmod(ordinal, len(alphabet))
                value[position] = alphabet[digit]
            return "".join(value)
        if self.mode == "reverse":
            return self.input_value[::-1]
        return self.input_value  # repeat emits N records, not one concatenation

    def records(self, preview_limit=None):
        plan = self.plan()
        signature = self.signature()
        count = plan["count"] if preview_limit is None else min(plan["count"], preview_limit)
        for ordinal in range(self.start, self.start + count):
            payload = self.payload(ordinal)
            value = self.prefix + payload + self.suffix
            handle = str(ordinal).zfill(self.handle_width)
            values = dict(value=value, payload=payload, ordinal=str(ordinal), handle=handle,
                          object_name=self.object_name, input_name=self.input_name,
                          flow_name=self.flow_name, output_name=self.output_name)
            body, title = render(self.body_template, values), render(self.title_template, values)
            if not body.strip() or not title.strip():
                raise ValueError(f"Record {ordinal} has an empty title/body. Add a descriptive template.")
            yield {"title": title, "body": body, "enabled": self.enabled, "tags": self.tags,
                   "generation_key": f"{signature}:{ordinal}",
                   "provenance": {"kind": "generated", "recipe": asdict(self), "ordinal": str(ordinal),
                                  "handle": handle, "payload": payload, "rendered": value,
                                  "fnv1a64": fnv1a64(value), "separator": self.separator}}

    @observed('generator.preview')
    def preview(self):
        return {"plan": self.plan(), "records": list(self.records(10))}
