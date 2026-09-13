import copy
import unittest

from pandr.errors import BudgetExceeded, ResolutionError, ValidationError
from pandr.expressions import Function
from pandr.geometry import measurements, number_is, number_to_float, number_to_text, validate_points


P = [{"id": "p", "x": 3, "y": 7}]


def event(source, channel="v", plane="horizontal", **options):
    result = measurements(Function.parse(source), P, channel=channel, plane=plane, **options)[0]
    assert result["status"] == "resolved", result
    return result["events"][0]


class GeometryTests(unittest.TestCase):
    def test_identity_four_rays_and_contacts(self):
        cases = [("v", "horizontal", "7", "right", ["0", "7"], ["7", "7"]),
                 ("h", "horizontal", "-3", "left", ["3", "0"], ["0", "0"]),
                 ("v", "vertical", "-7", "down", ["0", "7"], ["0", "0"]),
                 ("h", "vertical", "3", "up", ["3", "0"], ["3", "3"])]
        for channel, plane, distance, direction, contact, endpoint in cases:
            with self.subTest(channel=channel, plane=plane):
                result = event("x", channel, plane)
                self.assertTrue(number_is(result["displacement"], distance))
                self.assertEqual(direction, result["direction"])
                self.assertEqual(contact, result["contact"])
                self.assertEqual(endpoint, [number_to_text(n) for n in result["endpoint"]])

    def test_transformed_four_rays(self):
        for channel, plane, distance in [("v", "horizontal", "2"), ("h", "horizontal", "-8"),
                                         ("v", "vertical", "-2"), ("h", "vertical", "8")]:
            self.assertTrue(number_is(event("x+5", channel, plane)["displacement"], distance))
        self.assertTrue(number_is(event("2*x+1", "h")["displacement"], "-7/2"))
        self.assertEqual(event("-x-5", "v", "vertical")["direction"], "down")
        self.assertEqual(event("x+11", "v", "vertical")["direction"], "up")
        self.assertEqual(event("x-3", "h", "vertical")["direction"], "coincident")

    def test_transform_composition_order_and_immutability(self):
        original = Function.parse("x")
        transformed = original.translate(dx="2", dy="5").postcompose(Function.parse("2*x"))
        self.assertTrue(number_is(transformed.evaluate("3"), "12"))
        self.assertTrue(number_is(original.evaluate("3"), "3"))
        self.assertTrue(number_is(Function.parse("x+1").precompose(Function.parse("2*x")).evaluate("3"), "7"))
        self.assertTrue(number_is(Function.parse("x+1").postcompose(Function.parse("2*x")).evaluate("3"), "8"))
        self.assertTrue(number_is(Function.parse("x+1").reflect_x().evaluate("3"), "-4"))
        self.assertTrue(number_is(Function.parse("x+1").reflect_y().evaluate("3"), "-2"))
        mutated = original.tree
        mutated["op"] = "bad"
        self.assertEqual(original.display(), "x")
        restored = Function.from_dict(transformed.to_dict())
        self.assertEqual(restored.to_dict(), transformed.to_dict())

    def test_cancellation_poles_and_zero_scaling_preserve_domain(self):
        function = Function.parse("(x*x-1)/(x-1)")
        with self.assertRaises(ResolutionError):
            function.evaluate("1")
        self.assertTrue(number_is(function.evaluate("2"), "3"))
        with self.assertRaises(ResolutionError):
            function.scale(sy="0").evaluate("1")
        with self.assertRaises(ResolutionError):
            function.translate(dx="2").evaluate("3")
        with self.assertRaises(ResolutionError):
            Function.compose(Function.parse("0"), Function.parse("1/x")).evaluate("0")
        with self.assertRaises(ResolutionError):
            Function.compose(Function.parse("1/x"), Function.parse("x-3")).evaluate("3")
        result = measurements(Function.parse("(x-3)/(x-3)"), P, plane="vertical", channel="h")[0]
        self.assertEqual(result["status"], "undefined")
        result = measurements(Function.parse("(x*x-1)/(x-1)"), [{"id":"q","x":3,"y":2}])[0]
        self.assertEqual(result["status"], "no_root")

    def test_certified_polynomial_roots_and_all_policies(self):
        function = Function.parse("x*x")
        self.assertEqual(measurements(function, P)[0]["status"], "ambiguous")
        roots = measurements(function, P, policy="all")[0]["events"]
        self.assertEqual([e["direction"] for e in roots], ["left", "right"])
        self.assertAlmostEqual(number_to_float(roots[1]["displacement"]), 7**0.5)
        self.assertFalse(number_is(roots[1]["magnitude"], "3"))
        self.assertEqual(event("x*x", policy="leftmost")["direction"], "left")
        self.assertEqual(event("x*x", policy="rightmost")["direction"], "right")
        self.assertEqual(event("x*x", policy="nearest_contact")["direction"], "left")
        self.assertEqual(event("x*x", policy="explicit_branch", branch=1)["direction"], "right")
        self.assertEqual(event("x*x", policy="explicit_branch", branch=["2", "3"])["direction"], "right")
        self.assertEqual(measurements(function, P, policy="explicit_branch", branch=2)[0]["status"], "unresolved")
        repeated = event("(x-2)**2", channel="h")
        self.assertEqual(repeated["multiplicity"], 2)
        self.assertTrue(number_is(repeated["displacement"], "-1"))

    def test_constant_and_effective_domain_statuses(self):
        self.assertEqual(measurements(Function.parse("7"), P)[0]["status"], "infinitely_many")
        self.assertEqual(measurements(Function.parse("3"), P)[0]["status"], "no_root")
        self.assertEqual(measurements(Function.parse("0"), P, channel="h")[0]["status"], "infinitely_many")
        self.assertEqual(measurements(Function.parse("x*x+1"), P, channel="h")[0]["status"], "no_root")
        self.assertEqual(measurements(Function.parse("x"), P, window=("-2", "2"))[0]["status"], "no_root")
        self.assertEqual(measurements(Function.parse("1/(x-x)"), P)[0]["status"], "undefined")
        self.assertEqual(measurements(Function.parse("1/x"), P, plane="vertical")[0]["status"], "undefined")
        self.assertEqual(measurements(Function.parse("x"), P, plane="vertical", channel="h", window=("-2", "2"))[0]["status"], "no_root")

    def test_parser_rejects_code_and_limits_growth(self):
        for source in ["__import__('os')", "x.real", "[x]", "lambda:x", "True", "x//2"]:
            with self.subTest(source=source), self.assertRaises(ValidationError):
                Function.parse(source)
        with self.assertRaises(BudgetExceeded):
            Function.parse("(((2**16)**16)**16)**16")
        with self.assertRaises(BudgetExceeded):
            Function.parse("(x**16)**16")
        self.assertEqual(measurements(Function.parse("x*x"), P, budget=1)[0]["status"], "budget_exceeded")
        with self.assertRaises(ValidationError):
            Function.parse("x").scale(sx="0")
        self.assertTrue(number_is(Function.parse("0.1*x").evaluate("3"), "3/10"))
        with self.assertRaises(ValidationError):
            Function.parse("x").evaluate(0.1)

    def test_point_validation(self):
        self.assertEqual(validate_points([{"id":"p","x":"-3","y":"7"}]), [{"id":"p","x":-3,"y":7}])
        for points in [[{"id":"p","x":True,"y":7}], [{"id":"p","x":1,"y":7}],
                       [{"id":"p","x":4,"y":7}], P + P,
                       P + [{"id":"other","x":3,"y":7}]]:
            with self.assertRaises(ValidationError):
                validate_points(points)


if __name__ == "__main__":
    unittest.main()
