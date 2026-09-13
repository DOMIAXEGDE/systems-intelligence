"""Versioned schemas validate real persisted sessions, not hand-built examples."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import fastjsonschema

from pandr import Runtime
from pandr.alphabets import create_alphabet, edit_glyph
from pandr.contracts import COMMANDS, make_contract


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).resolve().parents[1] / "schemas"
        cls.validators = {name: fastjsonschema.compile(json.loads((directory / f"{name}.schema.json").read_text(encoding="utf-8")))
                          for name in ("session", "contract", "signal", "alphabet", "artifact")}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.runtime = Runtime.create(self.directory / "session.json")

    def assertValid(self, kind, data):
        self.validators[kind](data)

    def assertInvalid(self, kind, data):
        with self.assertRaises(fastjsonschema.JsonSchemaException):
            self.validators[kind](data)

    def test_real_minimal_and_populated_sessions(self):
        self.assertValid("session", self.runtime.snapshot())
        plan = self.runtime.plan(request_id="schema-fixture")
        for kind in ("image", "audio", "video", "code"):
            plan.add_layer(kind)
        plan.resolve("v17 + v7 + v3 + v23 + h-distance37 + v23")
        plan.render("text").render("image").render("audio", parameters={"samples_per_event": 64})
        plan.render("video", parameters={"fps": "8"}).render("code")
        plan.save_script("example", "from pandr import Runtime\n").set_notes("Schema fixture")
        receipt = self.runtime.commit(plan)
        state = self.runtime.snapshot()
        self.assertValid("session", state)
        for output in receipt["outputs"]:
            if output["type"] == "artifact":
                self.assertValid("artifact", output["data"])
        reopened = Runtime.open(self.runtime.path)
        self.assertValid("session", reopened.snapshot())
        replayed = reopened.replay(self.directory / "replay" / "session.json")
        self.assertEqual(replayed["status"], "verified")
        self.assertValid("session", Runtime.open(replayed["session"]).snapshot())

    def test_four_direction_events_and_transform_asts(self):
        plan = self.runtime.plan().resolve("v-right17 + v-down17 + h-left37 + h-up37")
        self.runtime.commit(plan)
        state = self.runtime.snapshot()
        self.assertEqual({event["direction"] for event in state["domain"]["last_events"]}, {"right", "left", "up", "down"})
        self.assertValid("session", state)
        plan = self.runtime.plan().set_function("x**2/(x-1)")
        plan.transform.translate(dx="2", dy="-3").transform.scale(sx="-2", sy="1/3")
        plan.transform.precompose("x+1").transform.postcompose("x**2")
        self.runtime.commit(plan)
        self.assertValid("session", self.runtime.snapshot())

    def test_all_runtime_commands_and_persisted_fabric_change(self):
        self.assertValid("contract", make_contract("all-commands", COMMANDS))
        points = [{"id": "replacement", "x": 3, "y": -5}]
        self.runtime.commit(self.runtime.plan().set_fabric(points))
        state = Runtime.open(self.runtime.path).snapshot()
        self.assertEqual(state["domain"]["fabric"]["points"], points)
        self.assertValid("session", state)
        invalid = copy.deepcopy(state)
        invalid["transition_log"][-1]["commands"][0]["points"][0]["x"] = "3"
        self.assertInvalid("session", invalid)

    def test_real_signal_delivery_and_retry_metadata(self):
        target = Runtime.create(self.directory / "consumer.json")
        plan = self.runtime.plan().connect("sequence", target.path)
        plan.resolve("v-down17 + h-up37")
        self.runtime.commit(plan)
        envelope = self.runtime.snapshot()["outbox"][0]
        self.assertValid("signal", envelope)
        self.assertEqual(self.runtime.drain_outbox()[0]["status"], "acknowledged")
        target.reload()
        self.assertValid("session", self.runtime.snapshot())
        self.assertValid("session", target.snapshot())
        self.assertValid("signal", self.runtime.snapshot()["outbox"][0])

    def test_registered_contract_and_versioned_alphabet(self):
        contract = make_contract("custom", {"resolve"})
        self.assertValid("contract", contract)
        alphabet = create_alphabet("custom", count=4, width=4, height=4)
        rows = ["0000", "0110", "0110", "0000"]
        revised = edit_glyph(alphabet, alphabet["symbols"][0], rows)
        self.assertValid("alphabet", revised)
        self.runtime.commit(self.runtime.plan().register_contract(contract).define_alphabet(revised))
        self.assertValid("session", self.runtime.snapshot())

    def test_unknown_nested_fields_and_old_profile_rejected(self):
        state = self.runtime.snapshot()
        for mutate in (
            lambda d: d["domain"]["geometry"].update(profile="contacts-horizontal-v1"),
            lambda d: d["domain"]["fabric"]["points"][0].update(unexpected=1),
            lambda d: d["domain"]["functions"]["main"]["ast"].update(code="print(1)"),
            lambda d: d["domain"]["layers"][0].update(enabled="true"),
            lambda d: d["domain"]["budgets"].update(max_points=True),
            lambda d: d.update(schema_version=1),
            lambda d: d["integrity"].update(domain_hash="wrong"),
        ):
            with self.subTest(mutation=mutate):
                invalid = copy.deepcopy(state)
                mutate(invalid)
                self.assertInvalid("session", invalid)

    def test_layer_limit_and_numeric_records(self):
        state = self.runtime.snapshot()
        state["domain"]["layers"] = [dict(state["domain"]["layers"][0], id=f"layer{i}") for i in range(18)]
        self.assertInvalid("session", state)
        self.runtime.commit(self.runtime.plan().resolve("v-down17"))
        for bad in ({"kind": "rational", "n": "017", "d": "1"},
                    {"kind": "rational", "n": "17", "d": "0"}):
            state = self.runtime.snapshot()
            state["domain"]["last_events"][0]["magnitude"] = bad
            self.assertInvalid("session", state)
        state = self.runtime.snapshot()
        state["domain"]["last_events"][0]["direction"] = "right"
        self.assertInvalid("session", state)

    def test_contract_signal_artifact_and_alphabet_invalids(self):
        contract = make_contract("test", {"resolve"})
        contract["allowed_commands"].append("exec")
        self.assertInvalid("contract", contract)
        alphabet = create_alphabet(count=4, width=4, height=4)
        alphabet["glyphs"][alphabet["symbols"][0]][0] = "not-pixels"
        self.assertInvalid("alphabet", alphabet)
        receipt = self.runtime.commit(self.runtime.plan().render("text", "schema"))
        artifact = receipt["outputs"][0]["data"]
        for path in ("../escape.txt", "C:\\escape.txt", "/tmp/escape.txt", "artifacts/../../escape.txt"):
            bad = copy.deepcopy(artifact)
            bad["path"] = path
            self.assertInvalid("artifact", bad)
        self.runtime.commit(self.runtime.plan().send(self.directory / "other.json", ["17"]))
        envelope = self.runtime.snapshot()["outbox"][0]
        for change in ({"sequence": True}, {"payload": [17]}, {"payload": ["017"]},
                       {"protocol": "pandr-signal-v1"}, {"max_hops": 257}):
            self.assertInvalid("signal", {**envelope, **change})


if __name__ == "__main__":
    unittest.main()
