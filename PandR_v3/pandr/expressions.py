"""Bounded exact expression trees with domain-preserving transformations.

Only Python's arithmetic *syntax* is read; no source is executed. SymPy is the
certified algebraic backend, and the original tree keeps every division guard
even when normalization cancels a factor or multiplies an expression by zero.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import sympy as sp

from .errors import BudgetExceeded, ResolutionError, ValidationError
from .mathsyntax import FUNCTIONS, ARITY, CONSTANTS, SYMBOLIC_COMPARE

X = sp.Symbol("x", real=True)
Y = sp.Symbol("y", real=True)
MAX_NODES = 256
MAX_DEPTH = 48
MAX_SOURCE = 4096
MAX_EXPONENT = 16
MAX_INTEGER_DIGITS = 256
MAX_DEGREE = 32


def rational(value: Any) -> sp.Rational:
    """Read an exact scalar. Binary floats are deliberately not semantic input."""
    if isinstance(value, bool):
        raise ValidationError("Boolean values are not numbers")
    if isinstance(value, sp.Rational):
        result = value
    elif isinstance(value, int):
        result = sp.Rational(value)
    elif isinstance(value, dict) and value.get("kind") == "rational":
        if set(value) - {"kind", "n", "d", "approx"}:
            raise ValidationError("Unexpected rational fields")
        numerator, denominator = value.get("n"), value.get("d")
        if not isinstance(numerator, str) or not isinstance(denominator, str):
            raise ValidationError("Rational numerator and denominator must be strings")
        if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", numerator) or not re.fullmatch(r"[1-9][0-9]*", denominator):
            raise ValidationError("Malformed rational record")
        if max(len(numerator), len(denominator)) > MAX_INTEGER_DIGITS:
            raise BudgetExceeded("Rational digit budget exceeded")
        result = sp.Rational(int(numerator), int(denominator))
    elif isinstance(value, str):
        value = value.strip()
        if len(value) > MAX_INTEGER_DIGITS * 2 + 2:
            raise BudgetExceeded("Scalar digit budget exceeded")
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:/[+-]?[0-9]+)?", value):
            raise ValidationError(f"Expected an exact integer, decimal, or fraction: {value!r}")
        try:
            result = sp.Rational(value)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise ValidationError("Invalid rational scalar") from exc
    else:
        raise ValidationError("Use an integer, exact decimal/fraction string, or rational record")
    if not result.is_finite:
        raise ValidationError("Scalar must be finite")
    if max(len(str(result.p)), len(str(result.q))) > MAX_INTEGER_DIGITS:
        raise BudgetExceeded("Scalar digit budget exceeded")
    return result


def _constant(value: Any) -> dict:
    val = rational(value)
    return {"op": "const", "n": str(val.p), "d": str(val.q)}


def _check_tree(tree: Any, depth=0, counter=None) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_NODES or depth > MAX_DEPTH:
        raise BudgetExceeded("Expression node/depth budget exceeded")
    if not isinstance(tree, dict):
        raise ValidationError("Expression nodes must be objects")
    op = tree.get("op")
    required = {"x": {"op"}, "y": {"op"}, "const": {"op", "n", "d"}, "neg": {"op", "arg"},
                "add": {"op", "left", "right"}, "sub": {"op", "left", "right"},
                "mul": {"op", "left", "right"}, "div": {"op", "left", "right"},
                "pow": {"op", "base", "exponent"}, "compose": {"op", "outer", "inner"},
                "power": {"op", "base", "exponent"}, "symbol": {"op", "name"},
                "call": {"op", "name", "args"}, "implicit": {"op", "arg"},
                "union": {"op", "args"}, "where": {"op", "test", "yes", "no"},
                "compare": {"op", "kind", "left", "right"},
                "and": {"op", "args"}, "or": {"op", "args"}, "not": {"op", "arg"}}
    if not isinstance(op, str) or op not in required or set(tree) != required[op]:
        raise ValidationError("Unknown expression operator or invalid fields")
    if op == "const":
        rational({"kind": "rational", "n": tree["n"], "d": tree["d"]})
    elif op == "symbol":
        if tree['name'] not in CONSTANTS:
            raise ValidationError('Unknown mathematical constant')
    elif op in ('call', 'union', 'and', 'or'):
        if op == 'call' and tree['name'] not in FUNCTIONS:
            raise ValidationError('Unknown mathematical function')
        low, high = ARITY[tree['name']] if op == 'call' else (1, 32)
        if not isinstance(tree['args'], list) or not low <= len(tree['args']) <= high:
            raise ValidationError('Invalid mathematical argument count')
        for arg in tree['args']:
            _check_tree(arg, depth + 1, counter)
    elif op in ('implicit', 'not'):
        _check_tree(tree['arg'], depth + 1, counter)
    elif op == 'where':
        for key in ('test', 'yes', 'no'):
            _check_tree(tree[key], depth + 1, counter)
    elif op == 'compare':
        if tree['kind'] not in SYMBOLIC_COMPARE:
            raise ValidationError('Unknown comparison')
        for key in ('left', 'right'):
            _check_tree(tree[key], depth + 1, counter)
    elif op == 'power':
        for key in ('base', 'exponent'):
            _check_tree(tree[key], depth + 1, counter)
        if tree['exponent']['op'] == 'const' and abs(rational({"kind": "rational", "n": tree['exponent']['n'], "d": tree['exponent']['d']})) > 4096:
            raise BudgetExceeded('Literal exponent exceeds the plotting resource budget')
    elif op == "neg":
        _check_tree(tree["arg"], depth + 1, counter)
    elif op in {"add", "sub", "mul", "div"}:
        _check_tree(tree["left"], depth + 1, counter)
        _check_tree(tree["right"], depth + 1, counter)
    elif op == "pow":
        exponent = tree["exponent"]
        if type(exponent) is not int or not 0 <= exponent <= MAX_EXPONENT:
            raise ValidationError(f"Powers require an integer from 0 to {MAX_EXPONENT}")
        _check_tree(tree["base"], depth + 1, counter)
    elif op == "compose":
        _check_tree(tree["outer"], depth + 1, counter)
        _check_tree(tree["inner"], depth + 1, counter)


def _degree_bound(tree):
    """Conservative numerator/denominator bounds before algebraic expansion."""
    op = tree["op"]
    if op not in {'x', 'y', 'const', 'neg', 'pow', 'compose', 'add', 'sub', 'mul', 'div'}:
        return 0, 0
    if op in ("x", "y"):
        return 1, 0
    if op == "const":
        return 0, 0
    if op == "neg":
        return _degree_bound(tree["arg"])
    if op == "pow":
        a, b = _degree_bound(tree["base"])
        return a * tree["exponent"], b * tree["exponent"]
    if op == "compose":
        a, b = _degree_bound(tree["outer"])
        c, d = _degree_bound(tree["inner"])
        return max(a, b) * max(c, d), max(a, b) * max(c, d)
    a, b = _degree_bound(tree["left"])
    c, d = _degree_bound(tree["right"])
    if op in {"add", "sub"}:
        return max(a + d, c + b), b + d
    if op == "mul":
        return a + c, b + d
    return a + d, b + c


def _height_bound(tree):
    """Bound coefficient growth before SymPy constructs large exact integers."""
    op = tree["op"]
    if op not in {'x', 'y', 'const', 'neg', 'pow', 'compose', 'add', 'sub', 'mul', 'div'}:
        return 1
    if op in ("x", "y"):
        return 1
    if op == "const":
        return max(abs(int(tree["n"])).bit_length(), int(tree["d"]).bit_length(), 1)
    if op == "neg":
        return _height_bound(tree["arg"])
    if op == "pow":
        return (_height_bound(tree["base"]) + 8) * max(1, tree["exponent"])
    if op == "compose":
        return _height_bound(tree["outer"]) + max(1, sum(_degree_bound(tree["outer"]))) * (_height_bound(tree["inner"]) + 8)
    return _height_bound(tree["left"]) + _height_bound(tree["right"]) + 8


def _compile(tree):
    op = tree["op"]
    if op == 'symbol': return CONSTANTS[tree['name']], []
    if op == 'implicit': return _compile(tree['arg'])
    if op == 'call':
        compiled = [_compile(arg) for arg in tree['args']]
        return FUNCTIONS[tree['name']](*(c[0] for c in compiled), evaluate=False), [g for c in compiled for g in c[1]]
    if op == 'power':
        base, bg = _compile(tree['base']); exponent, eg = _compile(tree['exponent'])
        return sp.Pow(base, exponent, evaluate=False), bg + eg
    if op == 'compare':
        left, lg = _compile(tree['left']); right, rg = _compile(tree['right'])
        return SYMBOLIC_COMPARE[tree['kind']](left, right), lg + rg
    if op in ('and', 'or'):
        compiled = [_compile(arg) for arg in tree['args']]
        return (sp.And if op == 'and' else sp.Or)(*(c[0] for c in compiled)), [g for c in compiled for g in c[1]]
    if op == 'not':
        arg, guards = _compile(tree['arg'])
        return sp.Not(arg), guards
    if op == 'where':
        test, tg = _compile(tree['test']); yes, yg = _compile(tree['yes']); no, ng = _compile(tree['no'])
        return sp.Piecewise((yes, test), (no, True)), tg + [sp.Piecewise((g, test), (1, True)) for g in yg] + [sp.Piecewise((1, test), (g, True)) for g in ng]
    if op == 'union':
        # Plot each component separately: a union must not intersect their domains.
        compiled = [Function._make(arg) for arg in tree['args']]
        return sp.Tuple(*(f.expression if f.is_implicit else f.expression - Y for f in compiled)), []
    if op == "x":
        return X, []
    if op == "y":
        return Y, []
    if op == "const":
        return sp.Rational(int(tree["n"]), int(tree["d"])), []
    if op == "neg":
        value, guards = _compile(tree["arg"])
        return -value, guards
    if op == "pow":
        value, guards = _compile(tree["base"])
        return value ** tree["exponent"], guards
    if op == "compose":
        outer, guards_o = _compile(tree["outer"])
        inner, guards_i = _compile(tree["inner"])
        # Standard P&R behavior: substitute inner into x.
        return outer.subs(X, inner), guards_i + [g.subs(X, inner) for g in guards_o]
    left, guards_l = _compile(tree["left"])
    right, guards_r = _compile(tree["right"])
    guards = guards_l + guards_r
    if op == "div":
        guards.append(right)
        return left / right if right != 0 else sp.S.Zero, guards
    return {"add": lambda: left + right, "sub": lambda: left - right,
            "mul": lambda: left * right}[op](), guards


def _display(tree):
    op = tree["op"]
    if op == 'symbol': return tree['name']
    if op in ('call', 'union'):
        return f"{tree.get('name', 'union')}({', '.join(_display(a) for a in tree['args'])})"
    if op == 'implicit': return f"implicit({_display(tree['arg'])})"
    if op == 'power': return f"({_display(tree['base'])}**{_display(tree['exponent'])})"
    if op == 'compare':
        symbol = dict(zip(SYMBOLIC_COMPARE, ('<', '<=', '>', '>=', '==', '!=')))[tree['kind']]
        return f"({_display(tree['left'])} {symbol} {_display(tree['right'])})"
    if op == 'where': return f"({_display(tree['yes'])} if {_display(tree['test'])} else {_display(tree['no'])})"
    if op in ('and', 'or'): return '(' + f' {op} '.join(_display(a) for a in tree['args']) + ')'
    if op == 'not': return f"(not {_display(tree['arg'])})"
    if op in ("x", "y"):
        return op
    if op == "const":
        return tree["n"] if tree["d"] == "1" else f'({tree["n"]}/{tree["d"]})'
    if op == "neg":
        return f'(-{_display(tree["arg"])})'
    if op == "pow":
        return f'({_display(tree["base"])}**{tree["exponent"]})'
    if op == "compose":
        return f'compose({_display(tree["outer"])}, {_display(tree["inner"])})'
    symbol = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[op]
    return f'({_display(tree["left"])}{symbol}{_display(tree["right"])})'


@dataclass(frozen=True)
class Function:
    """An immutable expression; transformations return new functions."""
    _serialized: str
    _history_serialized: str = "[]"

    @classmethod
    def _make(cls, tree, history=None):
        _check_tree(tree)
        def check_subexpressions(node):
            if isinstance(node, dict):
                if max(_degree_bound(node)) > MAX_DEGREE or _height_bound(node) > 4096:
                    raise BudgetExceeded('Expression symbolic growth budget exceeded')
                for value in node.values():
                    if isinstance(value, (dict, list)): check_subexpressions(value)
            elif isinstance(node, list):
                for value in node: check_subexpressions(value)
        check_subexpressions(tree)
        if max(_degree_bound(tree)) > MAX_DEGREE:
            raise BudgetExceeded(f"Expression exceeds conservative degree limit {MAX_DEGREE}")
        if _height_bound(tree) > 4096:
            raise BudgetExceeded("Expression coefficient growth budget exceeded")
        if history is not None and (not isinstance(history, list) or len(history) > 128):
            raise BudgetExceeded("Transformation history limit exceeded")
        return cls(json.dumps(tree, sort_keys=True, separators=(",", ":")),
                   json.dumps(history or [], sort_keys=True, separators=(",", ":")))

    @classmethod
    def parse(cls, text: str):
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("Function source must be a nonempty string")
        text = text.strip()
        # A single equation is an implicit residual, including vertical lines.
        if re.search(r'(?<![<>=!])=(?!=)', text):
            sides = re.split(r'(?<![<>=!])=(?!=)', text)
            if len(sides) != 2:
                raise ValidationError('An equation requires exactly one equals sign')
            return cls.implicit(f'({sides[0]})-({sides[1]})')
        if len(text) > MAX_SOURCE:
            raise BudgetExceeded("Function source limit exceeded")
        try:
            parsed = ast.parse(text, mode="eval")
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise ValidationError("Invalid function syntax") from exc
        if sum(1 for _ in ast.walk(parsed)) > MAX_NODES * 3:
            raise BudgetExceeded("Expression syntax budget exceeded")

        def convert(node):
            if isinstance(node, ast.Name):
                if node.id == "x":
                    return {"op": "x"}
                if node.id == "y":
                    return {"op": "y"}
                if node.id in CONSTANTS:
                    return {'op': 'symbol', 'name': node.id}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                name = node.func.id
                if name in FUNCTIONS:
                    return {'op': 'call', 'name': name, 'args': [convert(a) for a in node.args]}
                if name == 'implicit' and len(node.args) == 1:
                    return {'op': 'implicit', 'arg': convert(node.args[0])}
                if name == 'union':
                    return {'op': 'union', 'args': [convert(a) for a in node.args]}
                if name == 'compose' and len(node.args) == 2:
                    return {'op': 'compose', 'outer': convert(node.args[0]), 'inner': convert(node.args[1])}
                if name == 'where' and len(node.args) == 3:
                    return dict(op='where', **dict(zip(('test', 'yes', 'no'), map(convert, node.args))))
            if isinstance(node, ast.IfExp):
                return {'op': 'where', 'test': convert(node.test), 'yes': convert(node.body), 'no': convert(node.orelse)}
            if isinstance(node, ast.Compare):
                kinds = {ast.Lt: 'lt', ast.LtE: 'le', ast.Gt: 'gt', ast.GtE: 'ge', ast.Eq: 'eq', ast.NotEq: 'ne'}
                operands = [node.left] + node.comparators
                if any(type(op) not in kinds for op in node.ops):
                    raise ValidationError('Unsupported mathematical comparison')
                args = [{'op': 'compare', 'kind': kinds[type(op)], 'left': convert(operands[i]), 'right': convert(operands[i+1])} for i, op in enumerate(node.ops)]
                return args[0] if len(args) == 1 else {'op': 'and', 'args': args}
            if isinstance(node, ast.BoolOp):
                return {'op': 'and' if isinstance(node.op, ast.And) else 'or', 'args': list(map(convert, node.values))}
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return {'op': 'not', 'arg': convert(node.operand)}
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return _constant(ast.get_source_segment(text, node))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                child = convert(node.operand)
                return {"op": "neg", "arg": child} if isinstance(node.op, ast.USub) else child
            operators = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}
            if isinstance(node, ast.BinOp) and type(node.op) in operators:
                return {"op": operators[type(node.op)], "left": convert(node.left), "right": convert(node.right)}
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                if isinstance(node.right, ast.Constant) and type(node.right.value) is int and 0 <= node.right.value <= MAX_EXPONENT:
                    return {"op": "pow", "base": convert(node.left), "exponent": node.right.value}
                return {'op': 'power', 'base': convert(node.left), 'exponent': convert(node.right)}
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitXor):
                raise ValidationError("Use ** for exponentiation, not ^")
            raise ValidationError("Use mathematical expressions, named functions, equations, where(), or union(); Python code is not expression syntax")
        try:
            return cls._make(convert(parsed.body))
        except RecursionError as exc:
            raise BudgetExceeded("Expression nesting limit exceeded") from exc

    @classmethod
    def from_dict(cls, record: dict):
        if not isinstance(record, dict) or type(record.get("version")) is not int or record.get("version") != 1:
            raise ValidationError("Unsupported function record version")
        if set(record) - {"version", "ast", "normalized", "domain", "history", "digest"}:
            raise ValidationError("Unexpected function record fields")
        result = cls._make(record.get("ast"), record.get("history", []))
        if "digest" in record and record["digest"] != result.digest:
            raise ValidationError("Function digest does not match its expression")
        return result

    @property
    def tree(self):
        # A fresh tree prevents callers mutating an otherwise immutable function.
        return json.loads(self._serialized)

    @cached_property
    def compiled(self):
        expr, guards = _compile(self.tree)
        return (sp.cancel(expr) if not self.has_general_math else expr), tuple(sp.cancel(g) for g in guards)

    @property
    def has_general_math(self):
        def general(node):
            if isinstance(node, dict):
                return node.get('op') not in {'x', 'y', 'const', 'neg', 'pow', 'compose', 'add', 'sub', 'mul', 'div'} or any(general(v) for v in node.values() if isinstance(v, (dict, list)))
            return any(general(v) for v in node) if isinstance(node, list) else False
        return general(self.tree)

    @property
    def supports_exact_contacts(self):
        tree = self.tree
        if tree['op'] == 'implicit': tree = tree['arg']
        return not self._make(tree).has_general_math

    @property
    def is_implicit(self):
        def has_y(node):
            if isinstance(node, dict):
                return node.get('op') == 'y' or any(has_y(v) for v in node.values())
            return any(has_y(v) for v in node) if isinstance(node, list) else False
        return self.tree['op'] in ('implicit', 'union') or has_y(self.tree)

    @property
    def components(self):
        if self.tree['op'] == 'union':
            return tuple(part for arg in self.tree['args'] for part in self._make(arg).components)
        return (self,)

    @classmethod
    def implicit(cls, source):
        value = cls.parse(source) if isinstance(source, str) else source
        return value if value.is_implicit else cls._make({'op': 'implicit', 'arg': value.tree})

    @classmethod
    def combine(cls, *functions):
        return cls._make({'op': 'union', 'args': [(cls.parse(f) if isinstance(f, str) else f).tree for f in functions]})

    def sample(self, x, y=0):
        """Display-only floating-point evaluation, separate from exact records."""
        return self.numeric(float(x), float(y))

    @cached_property
    def numeric(self):
        from .mathsyntax import numeric_callable
        return numeric_callable(self.tree)

    @property
    def expression(self):
        return self.compiled[0]

    @property
    def guards(self):
        return self.compiled[1]

    @property
    def digest(self):
        return hashlib.sha256(self._serialized.encode("utf-8")).hexdigest()

    def to_dict(self):
        return {"version": 1, "ast": json.loads(self._serialized),
                "normalized": str(self.expression),
                "domain": {"nonzero": [str(g) for g in self.guards]},
                "history": json.loads(self._history_serialized), "digest": self.digest}

    def display(self):
        return _display(self.tree)

    def defined_at(self, x):
        for guard in self.guards:
            value = sp.cancel(guard.subs(X, x))
            if value == 0 or value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
                return False
            if value.is_zero is None and value.equals(0):
                return False
        value = self.expression.subs(X, x)
        return not value.has(sp.zoo, sp.nan, sp.oo, -sp.oo)

    def evaluate(self, x):
        if self.has_general_math:
            raise ResolutionError('General expressions use sample() for numerical values; exact evaluation requires rational/algebraic arithmetic')
        value = rational(x)
        if not self.defined_at(value):
            raise ResolutionError(f"Function is undefined at x={value}")
        result = sp.cancel(self.expression.subs(X, value))
        if result.is_algebraic is not True:
            raise ResolutionError('This value has no exact algebraic certificate; use sample() for a numerical plotting value')
        return numeric_record(result)

    def _transform(self, tree, operation, **parameters):
        history = json.loads(self._history_serialized)
        history.append({"operation": operation, **parameters})
        return self._make(tree, history)

    @staticmethod
    def compose(outer, inner):
        if not isinstance(outer, Function) or not isinstance(inner, Function):
            raise ValidationError("Composition requires two Function values")
        return outer._transform({"op": "compose", "outer": outer.tree, "inner": inner.tree},
                                "compose", inner_digest=inner.digest)

    def precompose(self, other):
        if self.tree['op'] == 'union':
            return Function.combine(*(f.precompose(other) for f in self.components))
        return Function.compose(self, other)

    def postcompose(self, other):
        if self.tree['op'] == 'union':
            return Function.combine(*(f.postcompose(other) for f in self.components))
        return Function.compose(other, self)

    def _map_coordinates(self, x_tree, y_tree):
        def replace(node):
            if isinstance(node, dict):
                if node.get('op') == 'x': return copy.deepcopy(x_tree)
                if node.get('op') == 'y': return copy.deepcopy(y_tree)
                return {key: replace(value) for key, value in node.items()}
            return [replace(v) for v in node] if isinstance(node, list) else node
        return replace(self.tree)

    def translate(self, dx="0", dy="0"):
        a, b = _constant(dx), _constant(dy)
        inner = {"op": "sub", "left": {"op": "x"}, "right": a}
        if self.tree['op'] == 'union':
            return Function.combine(*(f.translate(dx, dy) for f in self.components))
        if self.is_implicit:
            tree = self._map_coordinates(inner, {'op': 'sub', 'left': {'op': 'y'}, 'right': b})
            return self._transform(tree, 'translate', dx=str(rational(dx)), dy=str(rational(dy)))
        tree = {"op": "add", "left": {"op": "compose", "outer": self.tree, "inner": inner}, "right": b}
        return self._transform(tree, "translate", dx=str(rational(dx)), dy=str(rational(dy)))

    def scale(self, sx="1", sy="1"):
        if rational(sx) == 0:
            raise ValidationError("Horizontal scale cannot be zero")
        inner = {"op": "div", "left": {"op": "x"}, "right": _constant(sx)}
        if self.tree['op'] == 'union':
            return Function.combine(*(f.scale(sx, sy) for f in self.components))
        if self.is_implicit:
            if rational(sy) == 0:
                raise ValidationError('An implicit coordinate scale must be nonzero')
            tree = self._map_coordinates(inner, {'op': 'div', 'left': {'op': 'y'}, 'right': _constant(sy)})
            return self._transform(tree, 'scale', sx=str(rational(sx)), sy=str(rational(sy)))
        tree = {"op": "mul", "left": _constant(sy), "right": {"op": "compose", "outer": self.tree, "inner": inner}}
        return self._transform(tree, "scale", sx=str(rational(sx)), sy=str(rational(sy)))

    def reflect_x(self):
        return self.scale(sy="-1")

    def reflect_y(self):
        return self.scale(sx="-1")


def numeric_record(value) -> dict:
    value = sp.cancel(value)
    if value.is_Rational:
        return {"kind": "rational", "n": str(value.p), "d": str(value.q)}
    if value.is_real is False or value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        raise ResolutionError("Expected a finite real algebraic number")
    z = sp.Symbol("z")
    poly = sp.Poly(sp.minpoly(value, z), z, domain=sp.QQ)
    if poly.degree() > MAX_DEGREE:
        raise BudgetExceeded("Algebraic certificate degree budget exceeded")
    intervals = poly.intervals(eps=sp.Rational(1, 10**20))
    for index, ((lower, upper), _multiplicity) in enumerate(intervals):
        if bool(value >= lower) and bool(value <= upper):
            return {"kind": "algebraic", "expression": str(value),
                    "polynomial": [str(c) for c in poly.all_coeffs()],
                    "lower": str(lower), "upper": str(upper), "root_index": index,
                    "solver": f"sympy-{sp.__version__}", "approx": str(value.evalf(20))}
    raise ResolutionError("Could not certify algebraic value")


def _record_value(record):
    if not isinstance(record, dict) or record.get("kind") == "rational":
        return rational(record)
    if record.get("kind") != "algebraic":
        raise ValidationError("Unknown numeric record kind")
    coefficients = record.get("polynomial")
    if not isinstance(coefficients, list) or not 2 <= len(coefficients) <= MAX_DEGREE + 1:
        raise ValidationError("Algebraic record requires a bounded polynomial certificate")
    z = sp.Symbol("z")
    polynomial = sp.Poly.from_list([rational(c) for c in coefficients], z)
    lower, upper = rational(record.get("lower")), rational(record.get("upper"))
    if lower >= upper:
        raise ValidationError("Algebraic isolating interval must have positive width")
    roots = list(dict.fromkeys(polynomial.real_roots(radicals=False)))
    selected = [root for root in roots if bool(root > lower) and bool(root < upper)]
    if len(selected) != 1:
        raise ValidationError("Algebraic interval does not isolate one real root")
    return selected[0]


def number_to_float(record) -> float:
    if isinstance(record, dict) and record.get("kind") == "algebraic":
        # The approximation is display-only. Never used by equality/selection.
        return float(_record_value(record).evalf(17))
    return float(rational(record))


def number_is(record, expected) -> bool:
    left, right = _record_value(record), _record_value(expected)
    difference = sp.simplify(left - right)
    return bool(difference == 0 or difference.is_zero is True)


def number_to_text(record) -> str:
    if record.get("kind") == "rational":
        return str(rational(record))
    _record_value(record)  # Validate the certificate, never parse expression text.
    return record.get("expression", f"root in ({record['lower']}, {record['upper']})")
