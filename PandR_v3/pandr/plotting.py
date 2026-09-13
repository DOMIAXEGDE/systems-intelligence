"""Shared, finite-resolution plotting for the GUI and trusted Python scripts.

Samples are display data, never exact contact/sequence records. Callables can
implement custom special functions, numerical models, or parametric curves.
"""
from dataclasses import dataclass, field
import html
import math
from pathlib import Path

from .errors import BudgetExceeded, ValidationError
from .expressions import Function


@dataclass
class Curve:
    evaluator: object
    kind: str = 'explicit'
    label: str = ''
    parameter_range: tuple = (0.0, 2 * math.pi)

    @classmethod
    def explicit(cls, function, label=''):
        return cls(function, 'explicit', label)

    @classmethod
    def implicit(cls, function, label=''):
        return cls(function, 'implicit', label)

    @classmethod
    def parametric(cls, function, parameter_range=(0, 2 * math.pi), label=''):
        return cls(function, 'parametric', label, parameter_range)


@dataclass
class Plot:
    x_range: tuple
    y_range: tuple
    segments: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    diagnostics: list = field(default_factory=lambda: [
        'Numerical plot; finite sampling may miss isolated points, narrow features, or rapid oscillations. '
        'Increase resolution or narrow the viewport to inspect detail.'])

    @property
    def points(self):
        return [point for _, segment in self.segments for point in (*segment, None)]

    def save(self, path, size=(1000, 700)):
        """Export a plot as PNG or SVG, without an optional plotting dependency."""
        path = Path(path).resolve()
        width, height = size
        if not (200 <= width <= 4096 and 200 <= height <= 4096):
            raise ValidationError('Plot image dimensions must be between 200 and 4096')
        colors = ('#2563eb', '#e11d48', '#059669', '#9333ea', '#d97706')
        margin = 55
        def screen(point):
            x, y = point
            return (margin + (x-self.x_range[0])/(self.x_range[1]-self.x_range[0])*(width-2*margin),
                    height-margin-(y-self.y_range[0])/(self.y_range[1]-self.y_range[0])*(height-2*margin))
        lines = []
        for i in range(11):
            x = self.x_range[0] + (self.x_range[1]-self.x_range[0])*i/10
            y = self.y_range[0] + (self.y_range[1]-self.y_range[0])*i/10
            lines.extend([(screen((x, self.y_range[0])), screen((x, self.y_range[1])), '#e2e8f0'),
                          (screen((self.x_range[0], y)), screen((self.x_range[1], y)), '#e2e8f0')])
        if self.x_range[0] <= 0 <= self.x_range[1]:
            lines.append((screen((0, self.y_range[0])), screen((0, self.y_range[1])), '#64748b'))
        if self.y_range[0] <= 0 <= self.y_range[1]:
            lines.append((screen((self.x_range[0], 0)), screen((self.x_range[1], 0)), '#64748b'))
        for index, segment in self.segments:
            lines.append((screen(segment[0]), screen(segment[1]), colors[index % len(colors)]))
        text = ' | '.join(self.labels)[:140]
        bounds = f'x: {self.x_range[0]:g} to {self.x_range[1]:g}   y: {self.y_range[0]:g} to {self.y_range[1]:g}   (numerical plot)'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == '.svg':
            parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                     '<rect width="100%" height="100%" fill="white"/>']
            for a, b, color in lines:
                parts.append(f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" stroke="{color}" stroke-width="1.5"/>')
            for y, value in ((26, text), (height-18, bounds)):
                parts.append(f'<text x="55" y="{y}" font-family="sans-serif" font-size="13">{html.escape(value)}</text>')
            path.write_text('\n'.join(parts + ['</svg>']), encoding='utf-8')
        elif path.suffix.lower() == '.png':
            from PIL import Image, ImageDraw
            image = Image.new('RGB', size, 'white'); draw = ImageDraw.Draw(image)
            for a, b, color in lines:
                draw.line([a, b], fill=color, width=2)
            draw.text((margin, 18), text, fill='#0f172a')
            draw.text((margin, height-25), bounds, fill='#475569')
            image.save(path)
        else:
            raise ValidationError('Plot export supports .png and .svg')
        return path


def _bounds(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValidationError('A plot range requires two finite, increasing numbers')
    lo, hi = map(float, value)
    if not math.isfinite(lo) or not math.isfinite(hi) or not math.isfinite(hi-lo) or lo >= hi:
        raise ValidationError('A plot range requires two finite, increasing numbers')
    return lo, hi


def plot(functions, *, x_range=(-10, 10), y_range=(-10, 10), resolution=400,
         max_evaluations=2000000):
    """Plot Functions, strings, Curve objects, callables, or a mixture of these.

    Explicit callables take x; implicit callables take (x,y); parametric
    callables take t and return (x,y). Their execution is trusted Python.
    """
    if type(resolution) is not int or not 16 <= resolution <= 1200:
        raise ValidationError('Plot resolution must be an integer from 16 to 1200')
    if type(max_evaluations) is not int or max_evaluations < 1:
        raise ValidationError('Plot evaluation budget must be a positive integer')
    result = Plot(_bounds(x_range), _bounds(y_range))
    curves = []
    def add(value):
        if isinstance(value, str): value = Function.parse(value)
        if isinstance(value, Function):
            for component in value.components:
                curves.append(Curve(component, 'implicit' if component.is_implicit else 'explicit', component.display()))
        elif isinstance(value, Curve): curves.append(value)
        elif callable(value): curves.append(Curve.explicit(value, getattr(value, '__name__', 'custom function')))
        else: raise ValidationError('Expected a Function, expression, Curve, or Python callable')
    for value in functions if isinstance(functions, (list, tuple)) else [functions]: add(value)
    if not 1 <= len(curves) <= 32:
        raise ValidationError('A plot supports 1 to 32 component curves')
    count = 0
    def sample(evaluator, *args):
        nonlocal count
        count += 1
        if count > max_evaluations:
            raise BudgetExceeded('Plot evaluation budget exceeded; reduce resolution or number of components')
        try:
            value = evaluator(*args)
            if isinstance(value, (tuple, list)):
                value = tuple(float(v) for v in value)
                return value if len(value) == 2 and all(math.isfinite(v) for v in value) else None
            if isinstance(value, complex):
                if value.imag: return None
                value = value.real
            value = float(value)
            return value if math.isfinite(value) else None
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            return None
    xmin, xmax = result.x_range; ymin, ymax = result.y_range
    def segment(index, a, b):
        # Clip to the viewport (Liang-Barsky), avoiding huge screen coordinates.
        dx, dy = b[0]-a[0], b[1]-a[1]
        lower, upper = 0., 1.
        for p, q in ((-dx, a[0]-xmin), (dx, xmax-a[0]), (-dy, a[1]-ymin), (dy, ymax-a[1])):
            if not p:
                if q < 0: return
                continue
            t = q/p
            if p < 0: lower = max(lower, t)
            else: upper = min(upper, t)
        if lower <= upper:
            result.segments.append((index, ((a[0]+lower*dx, a[1]+lower*dy), (a[0]+upper*dx, a[1]+upper*dy))))
    for index, curve in enumerate(curves):
        if curve.kind not in ('explicit', 'implicit', 'parametric'):
            raise ValidationError('Unknown curve kind')
        function = Function.parse(curve.evaluator) if isinstance(curve.evaluator, str) else curve.evaluator
        if isinstance(function, Function):
            if curve.kind == 'implicit':
                # Repeated outer factors have the same zero set, but no sign change.
                tree = function.tree
                if tree['op'] == 'implicit': tree = tree['arg']
                if tree['op'] in ('sub', 'add') and tree['right'].get('op') == 'const' and tree['right']['n'] == '0':
                    tree = tree['left']
                while tree['op'] == 'implicit' or (tree['op'] == 'pow' and tree['exponent'] > 0) or (tree['op'] == 'call' and tree['name'] == 'abs'):
                    tree = tree['arg'] if tree['op'] == 'implicit' else tree['base'] if tree['op'] == 'pow' else tree['args'][0]
                evaluator = Function._make(tree).numeric
            else: evaluator = lambda x, f=function: f.sample(x)
        elif callable(function): evaluator = function
        else: raise ValidationError('Curve evaluator must be a mathematical expression or callable')
        result.labels.append(curve.label or (function.display() if isinstance(function, Function) else f'{curve.kind} {index+1}'))
        if curve.kind != 'implicit':
            lo, hi = _bounds(curve.parameter_range) if curve.kind == 'parametric' else (xmin, xmax)
            def point(t):
                value = sample(evaluator, t)
                return value if curve.kind == 'parametric' else ((t, value) if value is not None else None)
            def refine(a, pa, b, pb, depth=0):
                middle = (a+b)/2; pm = point(middle)
                if pa is None and pb is None and pm is None: return
                good = pa is not None and pb is not None and pm is not None
                if good:
                    deviation = max(abs(pm[0]-(pa[0]+pb[0])/2)/(xmax-xmin), abs(pm[1]-(pa[1]+pb[1])/2)/(ymax-ymin))
                    good = deviation < 0.25/resolution
                if good:
                    segment(index, pa, pm); segment(index, pm, pb)
                elif depth < 8:
                    refine(a, pa, middle, pm, depth+1); refine(middle, pm, b, pb, depth+1)
                # Never join across an unresolved jump, pole, or domain boundary.
            a, pa = lo, point(lo)
            for i in range(1, resolution+1):
                b = lo+(hi-lo)*i/resolution; pb = point(b)
                refine(a, pa, b, pb); a, pa = b, pb
        else:
            step_x, step_y = (xmax-xmin)/resolution, (ymax-ymin)/resolution
            grid = [[sample(evaluator, xmin+i*step_x, ymin+j*step_y) for i in range(resolution+1)] for j in range(resolution+1)]
            def root(a, b, va, vb):
                if va == 0: return a
                if vb == 0: return b
                if (va < 0) == (vb < 0): return None
                scale = max(abs(va), abs(vb), 1e-300)
                for _ in range(28):
                    middle = ((a[0]+b[0])/2, (a[1]+b[1])/2)
                    value = sample(evaluator, *middle)
                    if value is None: return None
                    if abs(value) <= scale*1e-8: return middle
                    if (value < 0) == (va < 0): a, va = middle, value
                    else: b, vb = middle, value
                return None  # A jump or pole is not a zero crossing.
            for j in range(resolution):
                for i in range(resolution):
                    vertices = [(xmin+i*step_x, ymin+j*step_y), (xmin+(i+1)*step_x, ymin+j*step_y),
                                (xmin+(i+1)*step_x, ymin+(j+1)*step_y), (xmin+i*step_x, ymin+(j+1)*step_y)]
                    values = [grid[j][i], grid[j][i+1], grid[j+1][i+1], grid[j+1][i]]
                    if any(v is None for v in values): continue
                    if all(v == 0 for v in values): continue
                    edges = []
                    for k in range(4):
                        point_value = root(vertices[k], vertices[(k+1)%4], values[k], values[(k+1)%4])
                        if point_value is not None and point_value not in edges: edges.append(point_value)
                    if len(edges) == 2: segment(index, *edges)
                    elif len(edges) == 4:
                        center = sample(evaluator, xmin+(i+0.5)*step_x, ymin+(j+0.5)*step_y)
                        if center is None: continue
                        pairs = ((0, 1), (2, 3)) if (center < 0) == (values[0] < 0) else ((0, 3), (1, 2))
                        for a, b in pairs: segment(index, edges[a], edges[b])
    return result
