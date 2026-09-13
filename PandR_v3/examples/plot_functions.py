"""Run inside P&R's Python panel, or with python examples/plot_functions.py.

Exports next to the active session (or examples/plots when run standalone).
"""
import math
import os
from pathlib import Path

from pandr import Curve, Function, Runtime, plot


def main():
    session = os.environ.get('PANDR_SESSION')
    output = Path(session).resolve().parent / 'plots' if session else Path(__file__).resolve().parent / 'plots'
    sine = Function.parse('sin(x)')
    if session:
        with Runtime.open(session) as pr:
            plan = pr.plan().set_function(sine)
            print(pr.commit(plan))
            picture = pr.plot(plan, x_range=(-2*math.pi, 2*math.pi), y_range=(-2, 2))
    else:
        picture = plot(sine, x_range=(-2*math.pi, 2*math.pi), y_range=(-2, 2))
    print(picture.save(output / 'sine-wave.png'))

    combined = Function.combine('sin(x)', 'abs(x)/2-1', 'x**2+y**2=4',
                                'sqrt(-x) if x < 0 else -sqrt(x)')
    picture = plot(combined, x_range=(-5, 5), y_range=(-3, 3))
    print(picture.save(output / 'combined-functions.png'))
    print(picture.save(output / 'combined-functions.svg'))

    # Any numerical algorithm can supply an evaluator; no new parser feature needed.
    custom = plot([
        Curve.explicit(lambda x: sum(math.sin(k*x)/k for k in range(1, 16)), 'Fourier sum'),
        Curve.implicit(lambda x, y: math.sin(x*y)-math.cos(x+y), 'sin(xy) = cos(x+y)'),
        Curve.parametric(lambda t: (2*math.cos(3*t), 2*math.sin(2*t)), label='Lissajous'),
    ], x_range=(-4, 4), y_range=(-3, 3), resolution=240)
    print(custom.save(output / 'custom-curves.png'))


if __name__ == '__main__': main()
