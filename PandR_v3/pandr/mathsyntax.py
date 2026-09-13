"""Named mathematical operations for the data-only expression language.

This registry deliberately contains functions, not Python name lookup or eval.
User-defined algorithms enter the plotting API as explicitly supplied callables.
"""
import math
import operator

import mpmath
import sympy as sp

FUNCTIONS = {
    name: getattr(sp, name) for name in (
        'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2',
        'sinh', 'cosh', 'tanh', 'asinh', 'acosh', 'atanh',
        'exp', 'log', 'sqrt', 'erf', 'erfc', 'gamma', 'loggamma',
        'floor', 'sign', 'sinc', 'besselj', 'bessely')
}
FUNCTIONS.update(abs=sp.Abs, ceil=sp.ceiling, min=sp.Min, max=sp.Max)
ARITY = {name: (1, 1) for name in FUNCTIONS}
ARITY.update(atan2=(2, 2), log=(1, 2), min=(1, 32), max=(1, 32),
             besselj=(2, 2), bessely=(2, 2))
CONSTANTS = {'pi': sp.pi, 'e': sp.E, 'E': sp.E}
COMPARE = {'lt': operator.lt, 'le': operator.le, 'gt': operator.gt,
           'ge': operator.ge, 'eq': operator.eq, 'ne': operator.ne}
SYMBOLIC_COMPARE = dict(zip(COMPARE, (sp.Lt, sp.Le, sp.Gt, sp.Ge, sp.Eq, sp.Ne)))
NUMERIC = {name: getattr(mpmath, name, None) for name in FUNCTIONS}
NUMERIC.update({name: getattr(math, name) for name in (
    'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'sinh', 'cosh',
    'tanh', 'asinh', 'acosh', 'atanh', 'exp', 'log', 'sqrt', 'erf', 'erfc',
    'gamma', 'floor', 'ceil')})
NUMERIC.update(abs=abs, min=lambda *args: min(args), max=lambda *args: max(args),
               sign=lambda x: (x > 0) - (x < 0),
               sinc=lambda x: math.sin(x) / x if x else 1.0)


def numeric_callable(tree):
    """Compile validated AST nodes to closures, retaining branch-local domains."""
    op = tree['op']
    if op == 'x': return lambda x, y: x
    if op == 'y': return lambda x, y: y
    if op == 'const':
        value = int(tree['n']) / int(tree['d'])
        return lambda x, y: value
    if op == 'symbol':
        value = float(CONSTANTS[tree['name']])
        return lambda x, y: value
    if op == 'call':
        args = [numeric_callable(arg) for arg in tree['args']]
        function = NUMERIC[tree['name']]
        return lambda x, y: function(*(arg(x, y) for arg in args))
    if op in ('neg', 'implicit'):
        arg = numeric_callable(tree['arg'])
        return (lambda x, y: -arg(x, y)) if op == 'neg' else arg
    if op in ('pow', 'power'):
        base = numeric_callable(tree['base'])
        exponent = (lambda x, y: tree['exponent']) if op == 'pow' else numeric_callable(tree['exponent'])
        return lambda x, y: base(x, y) ** exponent(x, y)
    if op == 'where':
        test, yes, no = (numeric_callable(tree[key]) for key in ('test', 'yes', 'no'))
        return lambda x, y: yes(x, y) if test(x, y) else no(x, y)
    if op == 'compose':
        outer, inner = (numeric_callable(tree[key]) for key in ('outer', 'inner'))
        return lambda x, y: outer(inner(x, y), y)
    if op == 'compare':
        left, right = (numeric_callable(tree[key]) for key in ('left', 'right'))
        compare = COMPARE[tree['kind']]
        return lambda x, y: compare(left(x, y), right(x, y))
    if op in ('and', 'or'):
        args = [numeric_callable(arg) for arg in tree['args']]
        return lambda x, y: (all if op == 'and' else any)(arg(x, y) for arg in args)
    if op == 'not':
        arg = numeric_callable(tree['arg'])
        return lambda x, y: not arg(x, y)
    left, right = (numeric_callable(tree[key]) for key in ('left', 'right'))
    operation = {'add': operator.add, 'sub': operator.sub,
                 'mul': operator.mul, 'div': operator.truediv}[op]
    return lambda x, y: operation(left(x, y), right(x, y))
