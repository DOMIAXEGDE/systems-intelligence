"""Plot geometry, domain breaks, mathematical syntax and persistence contracts."""
import json
import math
from pathlib import Path
import tempfile
import unittest

import fastjsonschema

from pandr import Curve, Function, Runtime, plot
from pandr.errors import BudgetExceeded, ResolutionError, ValidationError


class PlottingTests(unittest.TestCase):
    def test_explicit_math_and_composition(self):
        for source, x, expected in (
            ('sin(x)', math.pi/2, 1), ('abs(x)', -2, 2),
            ('sqrt(x)', 4, 2), ('x**(1/2)', 4, 2), ('x**-1', 2, .5),
            ('x**17', 1, 1), ('log(x, 2)', 8, 3), ('exp(x)', 0, 1),
            ('min(abs(x), 2)', -3, 2), ('max(x, 2)', -3, 2),
            ('sin(pi*x)', .5, 1), ('erf(x)', 0, 0), ('floor(x)', 1.9, 1),
            ('besselj(0, x)', 0, 1)):
            with self.subTest(source=source):
                f = Function.parse(source)
                self.assertAlmostEqual(f.sample(x), expected)
                self.assertEqual(Function.from_dict(f.to_dict()).digest, f.digest)
                self.assertEqual(Function.parse(f.display()).digest, f.digest)
        sine = Function.parse('sin(x)').postcompose(Function.parse('2*x'))
        self.assertAlmostEqual(sine.sample(math.pi/2), 2)
        with self.assertRaises(ResolutionError):
            Function.parse('sin(x)').evaluate('1')
        from pandr.geometry import measurements
        self.assertEqual(measurements(Function.parse('0*log(x)'), [{'id': 'p', 'x': -3, 'y': 7}])[0]['status'], 'unsupported')
        sampled = plot('sin(x)', x_range=(-7, 7), y_range=(-2, 2), resolution=80)
        self.assertGreater(len(sampled.segments), 100)
        for _, segment in sampled.segments:
            for x, y in segment: self.assertAlmostEqual(y, math.sin(x))

    def test_piecewise_domains_and_discontinuities(self):
        f = Function.parse('sqrt(-x) if x < 0 else log(x+1)')
        self.assertAlmostEqual(f.sample(-4), 2)
        self.assertAlmostEqual(f.sample(0), 0)
        self.assertAlmostEqual(Function.parse('where(-2 < x < 2, x, 0)').sample(3), 0)
        self.assertAlmostEqual(Function.parse('where(x < 0 or x > 2, 1, 0)').sample(3), 1)
        for source, boundary in (('1/(x-0.123)', .123), ('floor(x)', 0),
                                 ('-1 if x < 0.123 else 1', .123)):
            result = plot(source, x_range=(-1, 1), y_range=(-4, 4), resolution=40)
            self.assertTrue(result.segments)
            for _, (a, b) in result.segments:
                self.assertFalse(a[0] < boundary < b[0], (source, a, b))
        hole = plot('(x-0.125)/(x-0.125)', x_range=(0, 1), resolution=32)
        self.assertFalse(any(a[0] <= .125 <= b[0] for _, (a, b) in hole.segments))

    def test_implicit_circle_even_power_and_vertical_line(self):
        for source in ('x**2+y**2=1', '(x**2+y**2-1)**2=0'):
            result = plot(source, x_range=(-2, 2), y_range=(-2, 2), resolution=50)
            self.assertGreater(len(result.segments), 40)
            for _, segment in result.segments:
                for x, y in segment: self.assertAlmostEqual(x*x+y*y, 1, places=6)
        vertical = plot('x=0.123', x_range=(-1, 1), y_range=(-1, 1), resolution=40)
        self.assertGreater(len(vertical.segments), 30)
        self.assertTrue(all(abs(p[0]-.123) < 1e-7 for _, segment in vertical.segments for p in segment))
        pole = plot('1/(x-0.123)=0', x_range=(-1, 1), y_range=(-1, 1), resolution=40)
        self.assertEqual(pole.segments, [])

    def test_unions_and_implicit_coordinate_transforms(self):
        function = Function.combine('sqrt(x)', 'sqrt(-x)', 'x**2+y**2=1')
        result = plot(function, x_range=(-2, 2), y_range=(-2, 2), resolution=40)
        self.assertEqual({index for index, _ in result.segments}, {0, 1, 2})
        self.assertEqual(Function.parse(function.display()).digest, function.digest)
        circle = Function.parse('x**2+y**2=1').translate('2', '3').scale('2', '3')
        self.assertAlmostEqual(circle.sample(6, 9), 0)
        self.assertAlmostEqual(circle.sample(4, 12), 0)
        union = function.translate('1', '2')
        self.assertEqual(len(union.components), 3)

    def test_custom_algorithms_and_parametric_curves(self):
        result = plot([
            lambda x: math.sin(x) + math.sin(2*x)/2,
            Curve.implicit(lambda x, y: x*x+y*y-1),
            Curve.parametric(lambda t: (math.cos(t), math.sin(t)), (0, 2*math.pi)),
        ], x_range=(-2, 2), y_range=(-2, 2), resolution=40)
        self.assertEqual({index for index, _ in result.segments}, {0, 1, 2})
        for index, segment in result.segments:
            if index in (1, 2):
                for x, y in segment: self.assertAlmostEqual(x*x+y*y, 1, places=6)

    def test_session_schema_replay_plan_preview_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            runtime = Runtime.create(directory/'session.json')
            function = Function.combine('sin(x)', 'abs(x)', 'x**2+y**2=1', 'x if x < 0 else sqrt(x)')
            plan = runtime.plan().set_function(function)
            preview = runtime.plot(plan, resolution=40)
            self.assertEqual(runtime.revision, 0)
            runtime.commit(plan)
            runtime = Runtime.open(runtime.path)
            schema = json.loads((Path(__file__).resolve().parents[1]/'schemas/session.schema.json').read_text())
            fastjsonschema.compile(schema)(runtime.snapshot())
            self.assertEqual(runtime.replay(directory/'replay/session.json')['status'], 'verified')
            self.assertEqual(preview.segments, runtime.plot(resolution=40).segments)
            for extension in ('png', 'svg'):
                path = preview.save(directory/f'plot.{extension}')
                self.assertGreater(path.stat().st_size, 1000)
            self.assertTrue(runtime.plot_implicit('x*x+y*y-1', resolution=40).segments)
            self.assertTrue(runtime.plot_implicit(plan, resolution=40).segments)

    def test_expression_safety_and_plot_budgets(self):
        for source in ('__import__("os")', 'x.real', 'sin.__call__(x)', 'sin(x, shell=True)',
                       'open("file")', 'lambda x:x', 'sin()', 'unknown(x)'):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                Function.parse(source)
        with self.assertRaises(BudgetExceeded): plot('sin(x)', max_evaluations=10)
        with self.assertRaises(BudgetExceeded): Function.parse('sin((x**16)**16)')
        with self.assertRaises(ValidationError): plot('x', resolution=0)
        with self.assertRaises(ValidationError): plot('x', x_range=(1, -1))


if __name__ == '__main__': unittest.main()
