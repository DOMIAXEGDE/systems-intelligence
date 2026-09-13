"""Regenerate the self-contained Draft 7 schemas from the version 2 data model.

Run this file from any directory. Schema validation checks structural types;
the runtime additionally checks canonical numbers, hashes, primality, reference
identity, relative-path confinement, and transaction-dependent invariants.
"""
import json
from pathlib import Path


def obj(properties, required=None, **extra):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False, **extra}


def ref(name):
    return {"$ref": "#/definitions/" + name}


def arr(items, **extra):
    return {"type": "array", "items": items, **extra}


def enum(*values):
    return {"enum": list(values)}


def mapping(value):
    return {"type": "object", "additionalProperties": value}


def optional(value):
    return {"anyOf": [value, {"type": "null"}]}


TEXT = {"type": "string"}
ID = {"type": "string", "minLength": 1}
INT = {"type": "integer", "minimum": 0}
POS = {"type": "integer", "minimum": 1}
BOOL = {"type": "boolean"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
DEC = {"type": "string", "pattern": "^(0|[1-9][0-9]*)$", "maxLength": 13000}
SIGNED = {"type": "string", "pattern": "^(0|-?[1-9][0-9]*)$", "maxLength": 13000}
RAT = {"type": "string", "pattern": "^(0|-?[1-9][0-9]*)(/[1-9][0-9]*)?$", "maxLength": 26001}
FREE = {"type": "object"}
PATH = {"type": "string", "minLength": 1,
        "pattern": r"^(?![A-Za-z]:)(?![/\\])(?!.*(?:^|[/\\])\.\.(?:[/\\]|$)).+$"}
PLANE = enum("horizontal", "vertical")
CHANNEL = enum("v", "h")
KINDS = enum("geometry", "sequence", "numeric", "alphabet", "text", "image", "audio", "video", "code")
MEDIA = enum("text", "image", "audio", "video", "code")
POLICY = enum("unique", "all", "leftmost", "rightmost", "nearest_contact", "explicit_branch")
COMMANDS = ["set_function", "translate", "scale", "reflect_x", "reflect_y", "precompose", "postcompose",
            "resolve", "define_alphabet", "render", "add_layer", "configure_layer", "remove_layer",
            "save_script", "set_notes", "send", "receive", "set_geometry", "register_contract",
            "connect", "set_budgets", "set_fabric"]

D = {}
D["rational"] = obj({"kind": enum("rational"), "n": SIGNED,
                     "d": {"type": "string", "pattern": "^[1-9][0-9]*$"}})
D["number"] = {"oneOf": [ref("rational"), obj({"kind": enum("algebraic"), "expression": TEXT,
    "polynomial": arr(RAT, minItems=2, maxItems=33), "lower": RAT, "upper": RAT,
    "root_index": INT, "solver": ID, "approx": TEXT})]}
D["ast"] = {"oneOf": [obj({"op": enum("x", "y")}), obj({"op": enum("const"), "n": SIGNED,
    "d": {"type": "string", "pattern": "^[1-9][0-9]*$"}}),
    obj({"op": enum("neg"), "arg": ref("ast")}),
    obj({"op": enum("pow"), "base": ref("ast"), "exponent": {"type": "integer", "minimum": 0, "maximum": 32}}),
    obj({"op": enum("compose"), "outer": ref("ast"), "inner": ref("ast")}),
    obj({"op": enum("add", "sub", "mul", "div"), "left": ref("ast"), "right": ref("ast")})]}
D["ast"]["oneOf"].extend([
    obj({"op": enum("symbol"), "name": enum("pi", "e", "E")}),
    obj({"op": enum("call"), "name": enum("sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "exp", "log", "sqrt", "erf", "erfc",
        "gamma", "loggamma", "floor", "sign", "sinc", "besselj", "bessely", "abs", "ceil", "min", "max"),
        "args": arr(ref("ast"), minItems=1, maxItems=32)}),
    obj({"op": enum("power"), "base": ref("ast"), "exponent": ref("ast")}),
    obj({"op": enum("implicit", "not"), "arg": ref("ast")}),
    obj({"op": enum("union", "and", "or"), "args": arr(ref("ast"), minItems=1, maxItems=32)}),
    obj({"op": enum("compare"), "kind": enum("lt", "le", "gt", "ge", "eq", "ne"), "left": ref("ast"), "right": ref("ast")}),
    obj({"op": enum("where"), "test": ref("ast"), "yes": ref("ast"), "no": ref("ast")})])
D["function"] = obj({"version": enum(1), "ast": ref("ast"), "normalized": TEXT,
    "domain": obj({"nonzero": arr(TEXT)}), "history": arr(obj({"operation": enum("translate", "scale", "compose"),
        "dx": RAT, "dy": RAT, "sx": RAT, "sy": RAT, "inner_digest": HASH}, ["operation"])), "digest": HASH})
D["point"] = obj({"id": ID, "x": {"type": "integer"}, "y": {"type": "integer"}})
D["fabric"] = obj({"id": ID, "points": arr(ref("point"), minItems=1, maxItems=4096), "digest": HASH})
D["geometry"] = obj({"profile": enum("contacts-xy-v2"), "window": arr(RAT, minItems=2, maxItems=2), "root_policy": POLICY})
D["budgets"] = obj({key: POS for key in ("max_points", "max_events", "max_terms", "max_operations",
    "max_degree", "max_queue", "max_output_bytes", "max_ticks")})
D["layer"] = obj({"id": ID, "type": KINDS, "enabled": BOOL, "config": FREE, "state": FREE})
D["branch"] = {"anyOf": [INT, obj({"index": INT}), obj({"lower": RAT, "upper": RAT})]}
selector_props = {"channel": CHANNEL, "plane": PLANE,
    "magnitude": {"anyOf": [RAT, {"type": "integer"}, ref("rational")]},
    "displacement": {"anyOf": [RAT, {"type": "integer"}, ref("rational")]},
    "direction": enum("left", "right", "up", "down", "coincident"),
    "point_ids": arr(ID, uniqueItems=True), "branch": ref("branch"), "layer_id": ID}
D["selector"] = obj(selector_props, ["channel"], oneOf=[{"required": ["magnitude"]}, {"required": ["displacement"]}],
    allOf=[{"if": {"properties": {"direction": enum("up", "down")}, "required": ["direction"]},
            "then": {"properties": {"plane": enum("vertical")}}},
           {"if": {"properties": {"direction": enum("left", "right")}, "required": ["direction"]},
            "then": {"properties": {"plane": enum("horizontal")}}}])
D["event"] = obj({"point_id": ID, "channel": CHANNEL, "plane": PLANE,
    "direction": enum("left", "right", "up", "down", "coincident"),
    "displacement": ref("number"), "magnitude": ref("number"),
    "contact": arr(RAT, minItems=2, maxItems=2), "endpoint": arr(ref("number"), minItems=2, maxItems=2),
    "root_index": INT, "multiplicity": POS, "occurrence": INT, "selector": ref("selector")},
    ["point_id", "channel", "plane", "direction", "displacement", "magnitude", "contact", "endpoint", "root_index", "multiplicity"],
    allOf=[{"if": {"properties": {"plane": enum("vertical")}},
            "then": {"properties": {"direction": enum("up", "down", "coincident")}},
            "else": {"properties": {"direction": enum("left", "right", "coincident")}}}])
D["sequenceSource"] = {"anyOf": [TEXT, arr(ref("selector"), maxItems=4096)]}
D["sequenceOptions"] = obj({"plane": PLANE, "root_policy": POLICY, "policy": POLICY,
    "match_policy": enum("all", "unique", "first_ordered"), "branch": ref("branch")}, [])
D["sequence"] = obj({"source": ref("sequenceSource"), "selectors": arr(ref("selector")), "options": ref("sequenceOptions")})
D["alphabet"] = obj({"schema": enum("pandr-alphabet/1"), "id": {"type": "string", "minLength": 1, "maxLength": 128},
    "version": {"type": "integer", "minimum": 1, "maximum": 1000000000},
    "symbols": arr(ID, minItems=2, maxItems=1024, uniqueItems=True),
    "width": {"type": "integer", "minimum": 1, "maximum": 256},
    "height": {"type": "integer", "minimum": 1, "maximum": 256},
    "glyphs": mapping(arr({"type": "string", "pattern": "^[01]+$"}, minItems=1, maxItems=256)),
    "color_mode": enum("1"), "palette": arr(arr({"type": "integer", "minimum": 0, "maximum": 255}, minItems=4, maxItems=4), minItems=2, maxItems=2),
    "metrics": obj({"baseline": INT, "advance": POS}), "metadata": FREE, "digest": HASH, "previous_digest": HASH},
    ["schema", "id", "version", "symbols", "width", "height", "glyphs", "color_mode", "palette", "metrics", "metadata", "digest"])
D["predicate"] = {"oneOf": [obj({"op": enum("points_unchanged")}),
    obj({"op": enum("layer_limit"), "value": {"type": "integer", "minimum": 1, "maximum": 17}}, ["op"]),
    obj({"op": enum("output_count"), "value": INT}),
    obj({"op": enum("function_is", "domain_hash"), "digest": HASH})]}
D["contract"] = obj({"id": ID, "version": POS, "allowed_commands": arr(enum(*COMMANDS), uniqueItems=True),
    "preconditions": arr(ref("predicate")), "postconditions": arr(ref("predicate")),
    "max_commands": {"type": "integer", "minimum": 1, "maximum": 4096}, "digest": HASH})
D["blob"] = obj({"path": PATH, "sha256": HASH, "bytes": INT})
artifact = {"schema": enum("pandr-artifact/1"), "kind": MEDIA, "path": PATH, "mime": ID,
    "sha256": HASH, "bytes": INT, "total_bytes": INT, "parameters": FREE, "dependencies": arr(ref("blob")),
    "renderer": enum("pandr-utf8-v1", "pandr-pixels-v1", "pandr-integer-pcm-v1", "pandr-apng-v1"),
    "encoding": enum("utf-8"), "normalization": enum("none"), "language": TEXT,
    "validation": enum("unvalidated", "valid", "invalid"), "executed": enum(False),
    "pillow_version": TEXT, "zlib_version": TEXT, "width": POS, "height": POS, "mode": TEXT,
    "compositing": enum("replace-rgba"), "scan_order": enum("row-major"), "pixel_sha256": HASH,
    "glyph_stream": PATH, "alphabet_id": ID, "alphabet_version": POS, "alphabet_digest": HASH,
    "sample_rate": POS, "channels": {"type": "integer", "minimum": 1, "maximum": 2}, "bit_depth": enum(16),
    "sample_frames": INT, "sample_values": INT, "duration": RAT, "pcm_sha256": HASH,
    "event_timing": arr(obj({"event": INT, "rank": INT, "start_sample": INT, "sample_count": INT, "frequency": POS})),
    "fps": RAT, "frame_count": POS, "encoded_frame_count": POS, "audio_muxed": enum(False), "timeline": PATH,
    "frame_pixel_hashes": arr(HASH),
    "provenance": obj({"domain_hash": HASH, "request_id": ID, "alphabet_digest": HASH, "codec": ID})}
D["artifact"] = obj(artifact, ["schema", "kind", "path", "mime", "sha256", "bytes", "total_bytes", "parameters", "dependencies", "renderer"])
D["signalOptions"] = obj({"hop_count": INT, "max_hops": {"type": "integer", "minimum": 1, "maximum": 256},
    "destination_layer": ID, "expected_revision": optional(INT), "expected_domain_hash": optional(HASH), "correlation_id": ID}, [])
D["route"] = obj({"source_layer": ID, "destination": ID, "options": ref("signalOptions")})
signal = {"protocol": enum("pandr-signal-v2"), "message_id": HASH, "transaction_id": ID,
    "source_session": ID, "source_layer": ID, "source_port": enum("output"), "source_revision": INT,
    "source_domain_hash": HASH, "destination": ID, "destination_layer": ID, "destination_port": enum("input"),
    "expected_revision": optional(INT), "expected_domain_hash": optional(HASH), "stream_id": HASH,
    "sequence": INT, "logical_tick": INT, "hop_count": INT,
    "max_hops": {"type": "integer", "minimum": 1, "maximum": 256},
    "payload_type": enum("rank-stream", "artifact-reference", "text", "events"), "payload": {}, "payload_digest": HASH,
    "codec": enum("domalec-shortlex-repo-v1"), "correlation_id": ID,
    "status": enum("pending", "acknowledged", "failed"), "delivery_detail": TEXT}
D["signal"] = obj(signal, [key for key in signal if key not in ("status", "delivery_detail")], allOf=[
    {"if": {"properties": {"payload_type": enum("rank-stream")}}, "then": {"properties": {"payload": arr(DEC, maxItems=10000)}}},
    {"if": {"properties": {"payload_type": enum("text")}}, "then": {"properties": {"payload": TEXT}}},
    {"if": {"properties": {"payload_type": enum("events")}}, "then": {"properties": {"payload": arr(ref("event"), maxItems=10000)}}}])
D["received"] = obj({"message_id": HASH, "stream_id": HASH, "payload_digest": HASH, "sequence": INT})
D["domain"] = obj({"fabric": ref("fabric"), "functions": {"type": "object", "required": ["main"], "additionalProperties": ref("function")},
    "geometry": ref("geometry"), "layers": arr(ref("layer"), minItems=1, maxItems=17), "alphabets": mapping(ref("alphabet")),
    "selected_alphabet": ID, "contracts": mapping(ref("contract")), "sequences": arr(ref("sequence")),
    "routes": arr(ref("route")), "budgets": ref("budgets"), "tick": INT,
    "last_events": arr(ref("event")), "last_ranks": arr(DEC, maxItems=10000), "received": arr(ref("received"))})

command_shapes = [obj({"op": enum("set_function", "precompose", "postcompose"), "function": ref("function")}),
    obj({"op": enum("translate"), "dx": RAT, "dy": RAT}), obj({"op": enum("scale"), "sx": RAT, "sy": RAT}),
    obj({"op": enum("reflect_x", "reflect_y")}),
    obj({"op": enum("resolve"), "sequence": ref("sequenceSource"), "options": ref("sequenceOptions")}),
    obj({"op": enum("define_alphabet"), "manifest": ref("alphabet")}),
    obj({"op": enum("render"), "kind": MEDIA, "payload": {}, "parameters": FREE}),
    obj({"op": enum("add_layer"), "type": KINDS, "id": ID, "config": FREE}),
    obj({"op": enum("configure_layer"), "id": ID, "changes": obj({"enabled": BOOL, "config": FREE}, [])}),
    obj({"op": enum("remove_layer"), "id": ID}), obj({"op": enum("save_script"), "id": ID, "source": TEXT}),
    obj({"op": enum("set_notes"), "text": TEXT}),
    obj({"op": enum("set_geometry"), "changes": obj({"window": arr(RAT, minItems=2, maxItems=2), "root_policy": POLICY}, [])}),
    obj({"op": enum("set_fabric"), "points": arr(ref("point"), minItems=1, maxItems=4096)}),
    obj({"op": enum("set_budgets"), "changes": obj(D["budgets"]["properties"], [])}),
    obj({"op": enum("register_contract"), "contract": ref("contract")}),
    obj({"op": enum("connect"), **D["route"]["properties"]}),
    obj({"op": enum("send"), "destination": ID, "payload": {}, "payload_type": signal["payload_type"], "options": ref("signalOptions")}),
    obj({"op": enum("receive"), "envelope": ref("signal")})]
D["command"] = {"oneOf": command_shapes}
D["output"] = {"oneOf": [obj({"type": enum("events"), "data": arr(ref("event"))}),
    obj({"type": enum("artifact"), "data": ref("artifact")}), obj({"type": enum("alphabet"), "data": ref("alphabet")}),
    obj({"type": enum("signal"), "data": ref("signal")}), obj({"type": enum("received"), "data": obj({"message_id": HASH})})]}
D["receipt"] = obj({"transaction_id": ID, "status": enum("committed"), "from_revision": INT, "to_revision": POS,
    "before_domain_hash": HASH, "after_domain_hash": HASH, "plan_digest": HASH, "contract": ID, "contract_digest": HASH,
    "outputs": arr(ref("output")), "previous_digest": optional(HASH),
    "budget_usage": obj({"commands": INT, "events": INT}), "digest": HASH})
D["transition"] = obj({"request_id": ID, "contract": ID, "commands": arr(ref("command"), maxItems=4096),
    "from_revision": INT, "before_domain_hash": HASH, "after_domain_hash": HASH, "artifact_hashes": arr(HASH)})
D["session"] = obj({"schema": enum("pandr-session-v2"), "schema_version": enum(2), "session_id": ID, "revision": INT,
    "engine": obj({"version": enum("2.0.0"), "geometry": enum("contacts-xy-v2"), "codec": enum("domalec-shortlex-repo-v1")}),
    "domain": ref("domain"), "programs": mapping(obj({"id": ID, "source": TEXT, "digest": HASH})),
    "runs": mapping(obj({"status": enum("committed"), "plan_digest": HASH, "outputs": arr(ref("output"))})),
    "receipts": arr(ref("receipt")), "request_ledger": mapping(obj({"request_digest": HASH, "receipt": ref("receipt")})),
    "replay_checkpoint": obj({"domain": ref("domain"), "domain_hash": HASH}), "transition_log": arr(ref("transition")),
    "inbox": arr(ref("signal")), "outbox": arr({"allOf": [ref("signal"), {"required": ["status"]}]}),
    "delivery_ledger": mapping(obj({"payload_digest": HASH, "receipt": HASH})),
    "artifacts": mapping({"oneOf": [ref("artifact"), ref("blob")]}),
    "memory": obj({"workspace_notes": TEXT}), "ui": FREE,
    "integrity": obj({"domain_hash": HASH, "receipt_head": optional(HASH), "payload_hash": HASH}),
    "created_at": {"type": "string", "format": "date-time"}, "updated_at": {"type": "string", "format": "date-time"}})


def reachable(name):
    seen = set()
    def visit(node):
        if isinstance(node, dict):
            if "$ref" in node:
                key = node["$ref"].split("/")[-1]
                if key not in seen:
                    seen.add(key)
                    visit(D[key])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(ref(name))
    return {key: D[key] for key in sorted(seen)}


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    for name in ("session", "contract", "signal", "alphabet", "artifact"):
        schema = {"$schema": "http://json-schema.org/draft-07/schema#",
                  "$id": f"https://pandr.local/schemas/{name}.schema.json",
                  "title": f"P&R {name} ({'version 2' if name in ('session', 'signal') else 'version 1'})",
                  "description": "Structural validation. The runtime separately enforces canonical data, digests, prime coordinates, references, and cross-field invariants.",
                  **ref(name), "definitions": reachable(name)}
        (directory / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
