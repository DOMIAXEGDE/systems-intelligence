"""Exact contact measurements for the contacts-xy-v2 geometry profile."""
from __future__ import annotations

import copy
import re

import sympy as sp

from .errors import BudgetExceeded, ResolutionError, ValidationError
from .expressions import (Function, X, Y, MAX_DEGREE, rational, numeric_record,
                          number_to_float, number_to_text, number_is)

GEOMETRY_PROFILE = "contacts-xy-v2"
POLICIES = {"all", "unique", "leftmost", "rightmost", "nearest_contact", "explicit_branch"}
MAX_POINTS = 10000
MAX_COORDINATE = 2**63 - 1


def validate_points(points):
    if not isinstance(points, (list, tuple)) or len(points) > MAX_POINTS:
        raise ValidationError(f"Points must be a list with at most {MAX_POINTS} entries")
    result, ids, positions = [], set(), set()
    for point in points:
        if not isinstance(point, dict) or set(point) != {"id", "x", "y"}:
            raise ValidationError("Each point requires exactly id, x, and y")
        point_id = point["id"]
        if not isinstance(point_id, str) or not point_id or len(point_id) > 128 or point_id in ids:
            raise ValidationError("Point IDs must be nonempty, unique strings of at most 128 characters")
        coordinates = []
        for key in ("x", "y"):
            value = point[key]
            if isinstance(value, str):
                if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value) or len(value) > 20:
                    raise ValidationError("Point coordinates require decimal integer strings")
                value = int(value)
            if type(value) is not int or not 2 <= abs(value) <= MAX_COORDINATE or not sp.isprime(abs(value)):
                raise ValidationError(f"Point {point_id!r} {key} requires a signed prime within 64-bit bounds")
            coordinates.append(value)
        if tuple(coordinates) in positions:
            raise ValidationError("Duplicate point coordinates are not allowed")
        ids.add(point_id)
        positions.add(tuple(coordinates))
        result.append({"id": point_id, "x": coordinates[0], "y": coordinates[1]})
    return result


class _Budget:
    def __init__(self, budget):
        if budget is None:
            budget = {}
        if type(budget) is int:
            budget = {"max_operations": budget}
        if not isinstance(budget, dict):
            raise ValidationError("Solver budget must be an integer or object")
        self.limit = budget.get("max_operations", budget.get("solver_operations", 100000))
        self.degree = budget.get("max_degree", MAX_DEGREE)
        self.points = budget.get("max_points", MAX_POINTS)
        self.roots = budget.get("max_roots", 1024)
        for value in (self.limit, self.degree, self.points, self.roots):
            if type(value) is not int or value < 0:
                raise ValidationError("Solver budget limits must be nonnegative integers")
        self.degree = min(self.degree, MAX_DEGREE)
        self.used = 0

    def charge(self, units=1):
        self.used += units
        if self.used > self.limit:
            raise BudgetExceeded("Solver operation budget exceeded")


def _window(window):
    if not isinstance(window, (tuple, list)) or len(window) != 2:
        raise ValidationError("Resolver window requires [lower, upper]")
    lower, upper = rational(window[0]), rational(window[1])
    if lower >= upper:
        raise ValidationError("Resolver window lower bound must be less than upper bound")
    return lower, upper


def _root_candidates(equation, variable, window, budget, guards):
    if any(guard == 0 for guard in guards):
        return "undefined", [], "Function has an empty domain"
    expression = sp.cancel(equation)
    numerator, _denominator = expression.as_numer_denom()
    try:
        polynomial = sp.Poly(numerator, variable, domain=sp.QQ)
    except (sp.PolynomialError, sp.CoercionFailed):
        return "unsupported", [], "Only rational functions with rational coefficients are supported"
    if polynomial.is_zero:
        return "infinitely_many", [], "The contact line coincides with the function on infinitely many domain points"
    if polynomial.degree() == 0:
        return "no_root", [], "No intersection in the effective resolver domain"
    degree = polynomial.degree()
    if degree > budget.degree:
        raise BudgetExceeded("Polynomial degree budget exceeded")
    coefficient_bits = max(max(int(c.p).bit_length(), int(c.q).bit_length()) for c in polynomial.all_coeffs())
    if coefficient_bits > 4096:
        raise BudgetExceeded("Polynomial coefficient budget exceeded")
    budget.charge(degree**3 + coefficient_bits * degree)
    distinct = polynomial.real_roots(multiple=False, radicals=False)
    if len(distinct) > budget.roots:
        raise BudgetExceeded("Root count budget exceeded")
    roots = []
    for root, multiplicity in distinct:
        budget.charge(1 + len(guards))
        if bool(root >= window[0]) and bool(root <= window[1]):
            valid = True
            for g in guards:
                if sp.cancel(g.subs(variable, root)) == 0:
                    valid = False
                    break
            if valid:
                roots.append({"value": root, "multiplicity": multiplicity, "root_index": len(roots)})
    if not roots:
        return "no_root", [], "No intersection in the effective resolver domain"
    return "resolved", roots, "Certified distinct real roots in the effective resolver domain"


def _choose(roots, policy, contact_x, branch):
    if policy == "all":
        return "resolved", roots, None
    if policy == "unique":
        if len(roots) != 1:
            return "ambiguous", [], f"Expected one root; found {len(roots)}"
        return "resolved", roots, None
    if policy in {"leftmost", "rightmost"}:
        return "resolved", [roots[0 if policy == "leftmost" else -1]], None
    if policy == "nearest_contact":
        chosen = roots[0]
        distance = sp.Abs(chosen["value"] - contact_x)
        for candidate in roots[1:]:
            other_distance = sp.Abs(candidate["value"] - contact_x)
            if bool(other_distance < distance):
                chosen, distance = candidate, other_distance
        return "resolved", [chosen], None
    if type(branch) is int:
        if 0 <= branch < len(roots):
            return "resolved", [roots[branch]], None
        return "unresolved", [], "Explicit branch index is out of range"
    if isinstance(branch, dict):
        if set(branch) == {"index"}:
            return _choose(roots, policy, contact_x, branch["index"])
        if set(branch) == {"interval"}:
            branch = branch["interval"]
        elif set(branch) == {"lower", "upper"}:
            branch = [branch["lower"], branch["upper"]]
    if isinstance(branch, (list, tuple)) and len(branch) == 2:
        lower, upper = _window(branch)
        selected = [r for r in roots if bool(r["value"] >= lower) and bool(r["value"] <= upper)]
        if len(selected) == 1:
            return "resolved", selected, None
        return "unresolved", [], "Explicit branch interval must contain exactly one admissible root"
    raise ValidationError("explicit_branch requires an integer index or rational isolating interval")


def _event(point, channel, plane, endpoint_x, endpoint_y, root_index=0, multiplicity=1):
    contact = (sp.S.Zero, sp.Integer(point["y"])) if channel == "v" else (sp.Integer(point["x"]), sp.S.Zero)
    displacement = sp.cancel(endpoint_x - contact[0] if plane == "horizontal" else endpoint_y - contact[1])
    if displacement == 0:
        direction = "coincident"
    elif bool(displacement > 0):
        direction = "right" if plane == "horizontal" else "up"
    else:
        direction = "left" if plane == "horizontal" else "down"
    return {"point_id": point["id"], "channel": channel, "plane": plane,
            "direction": direction, "displacement": numeric_record(displacement),
            "magnitude": numeric_record(sp.Abs(displacement)), "contact": [str(v) for v in contact],
            "endpoint": [numeric_record(endpoint_x), numeric_record(endpoint_y)],
            "root_index": root_index, "multiplicity": int(multiplicity)}


def measurements(function, points, plane="horizontal", channel="v", policy="unique",
                 window=("-257", "257"), branch=None, budget=None):
    """Measure every point without silently discarding ambiguous/undefined cases.

    Horizontal v: (0,y)->(r,y), f(r)=y; horizontal h: (x,0)->(r,0), f(r)=0.
    Vertical v: (0,y)->(0,f(0)); vertical h: (x,0)->(x,f(x)). The resolver window
    bounds endpoint x on both planes. Plot/viewport sampling never enters here.
    """
    if not isinstance(function, Function):
        raise ValidationError("measurements requires a Function")
    if not isinstance(plane, str) or not isinstance(channel, str) or plane not in {"horizontal", "vertical"} or channel not in {"v", "h"}:
        raise ValidationError("Select plane horizontal/vertical and channel v/h")
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValidationError("Unknown root policy")
    validated = validate_points(points)
    domain = _window(window)
    allowance = budget if isinstance(budget, _Budget) else _Budget(budget)
    if len(validated) > allowance.points:
        raise BudgetExceeded("Point count budget exceeded")
    if function.tree['op'] == 'union':
        return [{'point_id': p['id'], 'status': 'unsupported', 'events': [],
                 'diagnostics': ['Combined curves are available for numerical plotting; exact contact resolution requires one component.']}
                for p in validated]
    if not function.supports_exact_contacts:
        return [{'point_id': p['id'], 'status': 'unsupported', 'events': [],
                 'diagnostics': ['This curve supports numerical plotting; certified contact solving currently requires rational/algebraic arithmetic.']}
                for p in validated]
    is_implicit = function.is_implicit
    equation = function.expression if is_implicit else function.expression - Y
    cache, results = {}, []
    for point in sorted(validated, key=lambda p: (p["x"], p["y"], p["id"])):
        result = {"point_id": point["id"], "status": "unresolved", "events": [], "diagnostics": []}
        try:
            allowance.charge()
            contact = (sp.S.Zero, sp.Integer(point["y"])) if channel == "v" else (sp.Integer(point["x"]), sp.S.Zero)

            if plane == "vertical":
                target_x = contact[0]
                if not domain[0] <= target_x <= domain[1]:
                    result.update(status="no_root", diagnostics=["Vertical endpoint x lies outside the resolver window"])
                    results.append(result)
                    continue
                if target_x not in cache:
                    sub_eq = sp.cancel(equation.subs(X, target_x))
                    sub_guards = [sp.cancel(g.subs(X, target_x)) for g in function.guards]
                    cache[target_x] = _root_candidates(sub_eq, Y, (-sp.oo, sp.oo), allowance, sub_guards)
                status, roots, message = cache[target_x]
                result.update(status=status, diagnostics=[message] if message else [])
                if status == "resolved":
                    status, selected, message = _choose(roots, policy, contact[1], branch)
                    result["status"] = status
                    if message:
                        result["diagnostics"].append(message)
                    result["candidate_count"] = len(roots)
                    for root in selected:
                        allowance.charge()
                        result["events"].append(_event(point, channel, plane, target_x, root["value"],
                                                       root["root_index"], root["multiplicity"]))
            else:
                target_y = contact[1]
                if target_y not in cache:
                    sub_eq = sp.cancel(equation.subs(Y, target_y))
                    sub_guards = [sp.cancel(g.subs(Y, target_y)) for g in function.guards]
                    cache[target_y] = _root_candidates(sub_eq, X, domain, allowance, sub_guards)
                status, roots, message = cache[target_y]
                result.update(status=status, diagnostics=[message] if message else [])
                if status == "resolved":
                    status, selected, message = _choose(roots, policy, contact[0], branch)
                    result["status"] = status
                    if message:
                        result["diagnostics"].append(message)
                    result["candidate_count"] = len(roots)
                    for root in selected:
                        allowance.charge()
                        result["events"].append(_event(point, channel, plane, root["value"], target_y,
                                                       root["root_index"], root["multiplicity"]))
        except BudgetExceeded as exc:
            result.update(status="budget_exceeded", events=[], diagnostics=[str(exc)])
        except (sp.PolynomialError, sp.CoercionFailed, NotImplementedError, TypeError) as exc:
            result.update(status="unsupported", events=[], diagnostics=[str(exc)])
        except ResolutionError as exc:
            result.update(status="unresolved", events=[], diagnostics=[str(exc)])
        results.append(result)
    return results
