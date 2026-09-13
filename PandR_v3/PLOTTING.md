# Function plotting in P&R

Enter an expression or equation in the Function editor and select **Apply function**.
Pan and zoom the canvas, and select **Detail** to increase sampling resolution.
Combined curves receive separate colours. The Python API also exports PNG and SVG
using the dependencies already installed by `build.ps1`.

| Curve | Function editor input |
| --- | --- |
| Sine | `sin(x)` |
| Non-smooth | `abs(x)`, `min(x, 2)`, `floor(x)` |
| Implicit circle | `x**2 + y**2 = 4` |
| Vertical line | `x = 2` or `implicit(x-2)` |
| Piecewise | `sqrt(-x) if x < 0 else log(x+1)` |
| Piecewise with conditions | `where(-2 < x < 2, sin(x), 0)` |
| Multiple curves | `union(sin(x), abs(x), implicit(x**2+y**2-4))` |
| Composition | `sin(abs(x))` or `compose(sin(x), abs(x))` |

Supported names include `sin`, `cos`, `tan`, inverse and hyperbolic variants,
`atan2`, `exp`, `log` (optional base), `sqrt`, `abs`, `min`, `max`, `floor`,
`ceil`, `sign`, `sinc`, `erf`, `erfc`, `gamma`, `loggamma`, `besselj`, and
`bessely`. Constants are `pi`, `e`, and `E`; powers use `**`, including negative,
fractional and variable exponents. `sinc(x)` means `sin(x)/x`, with value 1 at 0.
Conditions support comparisons, chained comparisons, `and`, `or`, and `not`.
Real-domain failures create gaps; they are not converted to complex curves.

`Function` values, including compositions, equations, and unions, persist in
sessions and replay. Explicit translations/scales transform the graph; implicit
translations/scales substitute both coordinates. Scaling an implicit coordinate
by zero is undefined. Composition substitutes the inner expression into `x`;
postcomposing an implicit residual transforms that residual, so it may change
its zero set. Union transformations apply to each component independently.

## Python scripts

```python
import math
import os
from pathlib import Path
from pandr import Runtime, Function

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    plan = pr.plan().set_function(Function.parse('sin(x)'))
    # Optional amplitude change:
    # plan.transform.postcompose(Function.parse('2*x'))
    print(pr.commit(plan))
    picture = pr.plot(plan, x_range=(-2*math.pi, 2*math.pi), y_range=(-2, 2), resolution=400)
    print(picture.save(Path(pr.path).parent / 'plots' / 'sine-wave.png'))
```

`pr.plot()` uses the committed function. Passing a plan previews its function
commands without committing it. Passing a `Function`, expression, or list plots
that value. `pr.plot_explicit(...)` is an alias for graph plotting;
`pr.plot_implicit('x*x+y*y-1', ...)` treats the supplied expression as a residual
equal to zero. Passing a plan to either uses its stored curve types.
Plots are returned as `Plot` objects; `.save(path)` exports an image. An `output=`
argument on `pr.plot` saves directly. These exports do not change session state
or register media artifacts. Open the image to view Python-generated plots;
the main GUI canvas shows the committed `Function`.

For mathematics outside the named expression language, supply Python callables.
This is the extension path for numerical solvers, special functions from another
library, generated constructions, and user-defined algorithms:

```python
from pandr import Curve, plot
import math

picture = plot([
    Curve.explicit(lambda x: sum(math.sin(k*x)/k for k in range(1, 30)), 'Fourier sum'),
    Curve.implicit(lambda x, y: math.sin(x*y) - math.cos(x+y)),
    Curve.parametric(lambda t: (2*math.cos(3*t), math.sin(2*t)), (0, 2*math.pi)),
], x_range=(-4, 4), y_range=(-3, 3), resolution=400)
picture.save('custom-curves.svg')
```

Explicit callbacks take `x`; implicit callbacks take `x, y`; parametric callbacks
take `t` and return `(x, y)`. Return `None` or a non-finite value outside a custom
domain. These are trusted Python programs, kept in the Python script editor;
callable objects themselves are not serialized into mathematical session data.

## Numerical plots and exact contacts

The plotter samples real values and adaptively refines explicit/parametric
segments. Implicit plots find zero crossings on a grid, check residuals, and
handle repeated outer powers. It avoids joining unresolved jumps and poles.
Each `Plot` includes diagnostics describing its numerical interpretation.

No finite sampler can guarantee every isolated zero, arbitrarily narrow branch,
or arbitrarily fast oscillation of an unrestricted function. Narrow the viewport
or increase resolution (16–1200 in Python); the default evaluation budget is two
million calls and can be adjusted with `max_evaluations`. Source/tree size and
symbolic growth budgets still protect the expression engine. Custom Python
callables allow more elaborate constructions within the execution time budget.

Contact measurements and sequence ranks still use certified rational/algebraic
results. They report unsupported or unresolved cases for functions outside that
solver; plot samples are never presented as exact contact values. Combined
curve contact solving is not implemented; measure an individual supported
component. Use `Function.sample(x, y=0)` for numerical values; `evaluate(x)`
continues to require an exact algebraic certificate.

Run `python examples/plot_functions.py` for sine, combined and custom examples.
