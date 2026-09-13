import unittest

from pandr.errors import BudgetExceeded, ResolutionError, ValidationError
from pandr.expressions import Function
from pandr.sequences import parse_sequence, resolve_sequence


POINTS = [{"id":"p0","x":2,"y":17}, {"id":"p1","x":3,"y":7},
          {"id":"p2","x":5,"y":3}, {"id":"p3","x":7,"y":23},
          {"id":"p4","x":37,"y":11}]


class SequenceTests(unittest.TestCase):
    def test_user_spellings_are_equivalent_and_repetitions_preserved(self):
        spellings = ["v17 v7 v3 v23 h37 v23", "v17 + v7 + v3 + v23 + h37 + v23",
                     "v17 + v7 + v3 + v23 + h-distance37 + v23"]
        self.assertEqual(parse_sequence(spellings[0]), parse_sequence(spellings[1]))
        self.assertEqual(parse_sequence(spellings[0]), parse_sequence(spellings[2]))
        events = resolve_sequence(spellings[0], Function.parse("x"), POINTS)
        self.assertEqual([e["point_id"] for e in events], ["p0","p1","p2","p3","p4","p3"])
        self.assertEqual([e["occurrence"] for e in events], list(range(6)))

    def test_vertical_modifiers_for_both_contacts(self):
        events = resolve_sequence("v-down7 + h-up3 + v-vertical7 + h-vertical3", Function.parse("x"), [POINTS[1]])
        self.assertEqual([e["direction"] for e in events], ["down", "up", "down", "up"])
        self.assertTrue(all(e["plane"] == "vertical" for e in events))
        events = resolve_sequence("v-up4 h-down8", Function.parse("11-19*x/3"), [POINTS[1]])
        self.assertEqual([e["direction"] for e in events], ["up", "down"])
        events = resolve_sequence("v-right7 h-left3", Function.parse("x"), [POINTS[1]])
        self.assertEqual([e["direction"] for e in events], ["right", "left"])

    def test_structured_signed_scopes_and_branch(self):
        selector = [{"channel":"h", "plane":"horizontal", "displacement":"-3", "point_ids":["p1"]}]
        self.assertEqual(resolve_sequence(selector, Function.parse("x"), POINTS)[0]["point_id"], "p1")
        selector = [{"channel":"v", "magnitude":"7", "direction":"down"}]
        self.assertEqual(resolve_sequence(selector, Function.parse("x"), [POINTS[1]])[0]["plane"], "vertical")
        with self.assertRaises(ValidationError):
            parse_sequence([{"channel":"v","plane":"horizontal","direction":"up","magnitude":"7"}])
        with self.assertRaises(ResolutionError):
            resolve_sequence([{"channel":"v","magnitude":"7","point_ids":["absent"]}], Function.parse("x"), POINTS)
        with self.assertRaises(ResolutionError):
            resolve_sequence([{"channel":"v","magnitude":"7","layer_id":"different"}], Function.parse("x"), POINTS)

    def test_match_policies_ordering_and_ambiguity_are_explicit(self):
        points = [{"id":"b","x":5,"y":7}, {"id":"a","x":3,"y":7}]
        events = resolve_sequence("v7", Function.parse("x"), points)
        self.assertEqual([e["point_id"] for e in events], ["a", "b"])
        self.assertEqual(resolve_sequence("v7", Function.parse("x"), points, match_policy="first_ordered")[0]["point_id"], "a")
        with self.assertRaises(ResolutionError):
            resolve_sequence("v7", Function.parse("x"), points, match_policy="unique")
        with self.assertRaises(ResolutionError):
            resolve_sequence("h3 v7", Function.parse("x*x"), [POINTS[1]])
        with self.assertRaises(ResolutionError):
            resolve_sequence("v7 v999", Function.parse("x"), points)
        with self.assertRaises(BudgetExceeded):
            resolve_sequence("v7", Function.parse("x"), points, budget=0)
        # Repeated selectors may reuse roots, but still share one sequence budget.
        with self.assertRaises(BudgetExceeded):
            resolve_sequence(" ".join(["v7"] * 100), Function.parse("x"), points, budget=50)

    def test_rejects_malformed_terms(self):
        for source in ["", "v017", "v-7", "v7+", "+v7", "v7++h3", "v7h3", "v7 junk", "v١٧", "V7", "v-up-7", "v7*h3"]:
            with self.subTest(source=source), self.assertRaises(ValidationError):
                parse_sequence(source)
        self.assertEqual(len(parse_sequence(" \t v7\r\n+h3 \n")), 2)


if __name__ == "__main__":
    unittest.main()
