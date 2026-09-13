"""Ordered exact selector parsing and atomic signal resolution."""
from __future__ import annotations

import copy
import re

from .errors import BudgetExceeded, ResolutionError, ValidationError
from .expressions import rational, numeric_record, number_is
from .geometry import measurements, validate_points, _Budget

MAX_TERMS = 4096
MAX_SEQUENCE_SOURCE = 65536
_TERM = re.compile(r"([vh])(?:-(distance|horizontal|vertical|left|right|up|down))?(0|[1-9][0-9]*)", re.ASCII)
_DIRECTIONS = {"left": "horizontal", "right": "horizontal", "up": "vertical", "down": "vertical"}


def _selector(value):
    if not isinstance(value, dict):
        raise ValidationError("Each structured selector must be an object")
    allowed = {"channel", "plane", "magnitude", "displacement", "direction", "point_ids", "branch", "layer_id"}
    if set(value) - allowed or not isinstance(value.get("channel"), str) or value.get("channel") not in {"v", "h"}:
        raise ValidationError("Unknown selector field or channel")
    if ("magnitude" in value) == ("displacement" in value):
        raise ValidationError("A selector requires exactly one of magnitude or displacement")
    direction = value.get("direction")
    if direction is not None and not isinstance(direction, str):
        raise ValidationError("Selector direction must be a string")
    plane = value.get("plane", _DIRECTIONS.get(direction, "horizontal"))
    if not isinstance(plane, str) or plane not in {"horizontal", "vertical"}:
        raise ValidationError("Unknown selector plane")
    if direction is not None and direction not in {*_DIRECTIONS, "coincident"}:
        raise ValidationError("Unknown selector direction")
    if direction in _DIRECTIONS and _DIRECTIONS[direction] != plane:
        raise ValidationError("Selector direction is incompatible with its plane")
    field = "magnitude" if "magnitude" in value else "displacement"
    scalar = rational(value[field])
    if field == "magnitude" and scalar < 0:
        raise ValidationError("Distance magnitude cannot be negative")
    result = {"channel": value["channel"], "plane": plane, field: numeric_record(scalar)}
    if direction is not None:
        result["direction"] = direction
    if "point_ids" in value:
        ids = value["point_ids"]
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValidationError("point_ids must be a list of unique nonempty strings")
        result["point_ids"] = list(ids)
    if "branch" in value:
        result["branch"] = copy.deepcopy(value["branch"])
    if "layer_id" in value:
        if not isinstance(value["layer_id"], str) or not value["layer_id"]:
            raise ValidationError("layer_id must be a nonempty string")
        result["layer_id"] = value["layer_id"]
    return result


def parse_sequence(source):
    """Parse ordered concatenation. '+' never performs numeric addition.

    ``v17``, ``v-distance17`` and ``v-horizontal17`` are equivalent.
    ``v-up17``/``v-down17`` filter vertical direction; ``v-vertical17`` accepts
    either vertical direction. The same modifiers apply to h-contact signals.
    """
    if isinstance(source, (list, tuple)):
        if not source or len(source) > MAX_TERMS:
            raise ValidationError(f"Sequences require 1..{MAX_TERMS} terms")
        return [_selector(term) for term in source]
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("Sequence source must be a nonempty string or selector list")
    if len(source) > MAX_SEQUENCE_SOURCE:
        raise BudgetExceeded("Sequence source length budget exceeded")
    terms, position = [], 0
    length = len(source)
    while position < length and source[position].isspace():
        position += 1
    while position < length:
        match = _TERM.match(source, position)
        if match is None:
            raise ValidationError(f"Invalid sequence term at character {position}")
        channel, modifier, digits = match.groups()
        plane = "vertical" if modifier in {"vertical", "up", "down"} else "horizontal"
        term = {"channel": channel, "plane": plane, "magnitude": digits}
        if modifier in _DIRECTIONS:
            term["direction"] = modifier
        terms.append(_selector(term))
        if len(terms) > MAX_TERMS:
            raise BudgetExceeded("Sequence term budget exceeded")
        position = match.end()
        whitespace_start = position
        while position < length and source[position].isspace():
            position += 1
        had_whitespace = position > whitespace_start
        if position == length:
            break
        if source[position] == "+":
            position += 1
            while position < length and source[position].isspace():
                position += 1
            if position == length:
                raise ValidationError("Sequence cannot end with '+'")
        elif not had_whitespace:
            raise ValidationError(f"Expected a separator at character {position}")
    return terms


def resolve_sequence(source, function, points, window=("-257", "257"), policy="unique",
                     match_policy="all", **kwargs):
    """Resolve all terms against one function; fail atomically on any bad term.

    ``layer_id`` may be supplied to require a structured selector's layer scope.
    The caller handles routing between layers; this resolver never ignores a
    selector naming another layer. Events carry zero-based occurrence indices.
    """
    selectors = parse_sequence(source)
    validated = validate_points(points)
    if not isinstance(match_policy, str) or match_policy not in {"all", "unique", "first_ordered"}:
        raise ValidationError("Unknown sequence match policy")
    if set(kwargs) - {"budget", "branch", "layer_id", "max_events"}:
        raise ValidationError("Unknown sequence resolution option")
    layer_id, budget = kwargs.get("layer_id"), kwargs.get("budget")
    allowance = _Budget(budget)
    max_events = kwargs.get("max_events", 100000)
    if type(max_events) is not int or max_events < 1:
        raise ValidationError("max_events must be a positive integer")
    known_ids = {point["id"] for point in validated}
    output, measurement_cache = [], {}
    for occurrence, selector in enumerate(selectors):
        allowance.charge()
        if "layer_id" in selector and selector["layer_id"] != layer_id:
            raise ResolutionError(f"Term {occurrence}: layer scope does not match the selected geometry layer")
        selected_ids = selector.get("point_ids")
        if selected_ids is not None and set(selected_ids) - known_ids:
            raise ResolutionError(f"Term {occurrence}: unknown point ID in scope")
        scoped = validated if selected_ids is None else [p for p in validated if p["id"] in selected_ids]
        branch = selector.get("branch", kwargs.get("branch"))
        root_policy = "explicit_branch" if branch is not None else policy
        # Canonical repr contains only validated scalar/list/dict structures.
        cache_key = (selector["channel"], selector["plane"], root_policy, repr(branch),
                     None if selected_ids is None else tuple(sorted(selected_ids)))
        if cache_key not in measurement_cache:
            measurement_cache[cache_key] = measurements(function, scoped, plane=selector["plane"],
                                                        channel=selector["channel"], policy=root_policy,
                                                        window=window, branch=branch, budget=allowance)
        matches = []
        for result in measurement_cache[cache_key]:
            if result["status"] == "no_root":
                continue
            if result["status"] != "resolved":
                error = BudgetExceeded if result["status"] == "budget_exceeded" else ResolutionError
                raise error(f"Term {occurrence}, point {result['point_id']}: {result['status']}; "
                            + "; ".join(result.get("diagnostics", [])))
            for event in result["events"]:
                allowance.charge()
                field = "magnitude" if "magnitude" in selector else "displacement"
                if not number_is(event[field], selector[field]):
                    continue
                if selector.get("direction") is not None and event["direction"] != selector["direction"]:
                    continue
                matches.append(copy.deepcopy(event))
        if not matches:
            raise ResolutionError(f"Term {occurrence}: no matching {selector['plane']} {selector['channel']} event")
        if match_policy == "unique" and len(matches) != 1:
            raise ResolutionError(f"Term {occurrence}: expected one match, found {len(matches)}")
        if match_policy == "first_ordered":
            matches = matches[:1]
        for event in matches:
            event["occurrence"] = occurrence
            event["selector"] = copy.deepcopy(selector)
            output.append(event)
        if len(output) > max_events:
            raise BudgetExceeded("Resolved event count budget exceeded")
    return output
