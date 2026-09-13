"""End-to-end transactions, immutable geometry, local contracts and replay."""
import copy
from pathlib import Path
import tempfile
import unittest

from pandr.canonical import content_digest
from pandr.contracts import make_contract
from pandr.errors import ContractError, ResolutionError, ValidationError
from pandr.expressions import Function
from pandr.runtime import Runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pandr-runtime-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = Runtime.create(self.root / "session.json")

    def test_user_sequence_resolves_and_renders_persisted_text(self):
        plan = self.runtime.plan(request_id="example")
        plan.resolve("v17 + v7 + v3 + v23 + h-distance37 + v23").render("text")
        preview = self.runtime.dry_run(plan)
        self.assertEqual(self.runtime.revision, 0)
        self.assertFalse((self.root / "artifacts").exists())
        receipt = self.runtime.commit(plan)
        events = receipt["outputs"][0]["data"]
        self.assertEqual([e["magnitude"]["n"] for e in events], ["17", "7", "3", "23", "37", "23"])
        self.assertEqual([e["occurrence"] for e in events], list(range(6)))
        self.assertEqual(events[3]["point_id"], events[5]["point_id"])
        artifact = receipt["outputs"][1]["data"]
        self.assertEqual((self.root / artifact["path"]).read_text(encoding="utf-8"), "0&\"6D6")
        self.assertEqual(preview["domain_hash"], receipt["after_domain_hash"])
        reopened = Runtime.open(self.runtime.path)
        self.assertEqual(reopened.snapshot()["domain"]["last_ranks"], ["17", "7", "3", "23", "37", "23"])
        self.assertEqual(reopened.validate()["status"], "valid")

    def test_four_rays_with_upward_and_downward_for_both_contacts(self):
        runtime = Runtime.create(self.root / "rays.json", points=[
            {"id": "positive", "x": 3, "y": 7}, {"id": "negative", "x": -3, "y": -7}])
        plan = runtime.plan().resolve("v-right7 h-left3 v-down7 h-up3 v-up7 h-down3")
        receipt = runtime.commit(plan)
        events = receipt["outputs"][0]["data"]
        self.assertEqual([e["direction"] for e in events], ["right", "left", "down", "up", "up", "down"])
        self.assertEqual([e["plane"] for e in events], ["horizontal", "horizontal", "vertical", "vertical", "vertical", "vertical"])
        self.assertEqual([e["displacement"]["n"] for e in events], ["7", "-3", "-7", "3", "7", "-3"])
        self.assertEqual([e["point_id"] for e in events], ["positive"] * 4 + ["negative"] * 2)

    def test_composed_transform_changes_function_without_moving_points(self):
        fabric = copy.deepcopy(self.runtime.snapshot()["domain"]["fabric"])
        before = self.runtime.measure(plane="vertical", channel="h")[0]["events"][0]
        plan = self.runtime.plan()
        plan.transform.translate(dx="2", dy="5")
        plan.transform.scale(sx="2", sy="3")
        plan.transform.precompose("x+1")
        plan.transform.postcompose("x**2")
        self.runtime.commit(plan, contract="operator-transform-v1")
        after = self.runtime.snapshot()["domain"]
        expected = Function.parse("x").translate("2", "5").scale("2", "3").precompose(Function.parse("x+1")).postcompose(Function.parse("x**2"))
        self.assertEqual(after["fabric"], fabric)
        self.assertEqual(after["functions"]["main"], expected.to_dict())
        self.assertNotEqual(before["displacement"], self.runtime.measure(plane="vertical", channel="h")[0]["events"][0]["displacement"])

    def test_postcondition_failure_rolls_back_domain_notes_and_artifacts(self):
        contract = make_contract("must-have-two-outputs", {"set_function", "set_notes", "render"})
        contract["postconditions"].append({"op": "output_count", "value": 2})
        contract["digest"] = content_digest(contract)
        self.runtime.commit(self.runtime.plan().register_contract(contract))
        before = self.runtime.snapshot()
        disk = self.runtime.path.read_bytes()
        plan = self.runtime.plan().set_function("x+10").set_notes("must roll back").render("text", "temporary")
        with self.assertRaises(ContractError):
            self.runtime.commit(plan, contract=contract["id"])
        self.assertEqual(self.runtime.snapshot(), before)
        self.assertEqual(self.runtime.path.read_bytes(), disk)
        self.assertFalse((self.root / "artifacts").exists())
        self.assertEqual(list((self.root / ".staging").iterdir()), [])

    def test_unmatched_later_sequence_term_rolls_back_earlier_transform(self):
        before = self.runtime.snapshot()
        plan = self.runtime.plan().set_function("x+1").resolve("v16 v999999")
        with self.assertRaises(ResolutionError):
            self.runtime.commit(plan)
        self.assertEqual(self.runtime.snapshot(), before)

    def test_seventeen_registered_layers_include_disabled_layers(self):
        plan = self.runtime.plan()
        for index in range(12):
            plan.add_layer("text", identifier=f"extra-{index}")
        plan.configure_layer("extra-0", {"enabled": False})
        self.runtime.commit(plan)
        self.assertEqual(len(self.runtime.snapshot()["domain"]["layers"]), 17)
        before = self.runtime.snapshot()
        with self.assertRaises(ValidationError):
            self.runtime.commit(self.runtime.plan().add_layer("image", identifier="eighteenth"))
        self.assertEqual(self.runtime.snapshot(), before)

    def test_replay_reproduces_transforms_events_and_all_five_artifact_formats(self):
        runtime = self.runtime
        plan = runtime.plan()
        for kind in ("image", "audio", "video", "code"):
            plan.add_layer(kind)
        plan.resolve("v17 v7 v3 v23 h37 v23")
        for kind in ("text", "image", "audio", "video", "code"):
            plan.render(kind)
        runtime.commit(plan)
        runtime.save_ui({"zoom": "2"})
        runtime.commit(runtime.plan().set_function("x+5").resolve("v-down12 h-up7", match_policy="first_ordered"))
        expected = runtime.snapshot()
        result = runtime.replay(self.root / "replayed" / "session.json")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["transitions"], 2)
        self.assertFalse(result["live_delivery"])
        replayed = Runtime.open(result["session"])
        self.assertEqual(replayed.snapshot()["domain"], expected["domain"])
        self.assertEqual(set(replayed.snapshot()["artifacts"]), set(expected["artifacts"]))
        self.assertEqual(replayed.validate()["status"], "valid")

    def test_export_import_and_save_as_preserve_and_verify_artifact_bytes(self):
        self.runtime.commit(self.runtime.plan().render("text", "P&R round trip"))
        bundle = self.runtime.export_bundle(self.root / "export.zip")
        imported = Runtime.import_bundle(bundle, self.root / "imported" / "session.json")
        copied = self.runtime.save_as(self.root / "copied" / "session.json")
        for runtime in (imported, copied):
            self.assertEqual(runtime.snapshot(), self.runtime.snapshot())
            self.assertEqual(runtime.validate()["status"], "valid")
        artifact = next(iter(imported.snapshot()["artifacts"].values()))
        (imported.path.parent / artifact["path"]).write_bytes(b"corrupt")
        with self.assertRaises(ValidationError):
            imported.validate()

    def test_script_argument_error_cannot_leave_a_running_execution(self):
        self.runtime.commit(self.runtime.plan().save_script("example", "print('hello')"))
        try:
            result = self.runtime.execute_script("example", timeout=0)
        except (ValueError, ValidationError):
            pass  # Rejecting before a run starts is also a valid lifecycle.
        else:
            self.assertEqual(result["status"], "error")
        state = self.runtime.reload()
        self.assertFalse(any(run["status"] == "running" for run in state["runs"].values()))

    def test_save_as_and_import_reject_existing_corrupt_blob_before_session_creation(self):
        self.runtime.commit(self.runtime.plan().render("text", "expected artifact"))
        artifact = next(iter(self.runtime.snapshot()["artifacts"].values()))
        bundle = self.runtime.export_bundle(self.root / "source.zip")
        for operation in ("save_as", "import"):
            with self.subTest(operation=operation):
                target = self.root / operation / "session.json"
                blob = target.parent / artifact["path"]
                blob.parent.mkdir(parents=True)
                blob.write_bytes(b"existing corrupt content")
                with self.assertRaises(ValidationError):
                    if operation == "save_as":
                        self.runtime.save_as(target)
                    else:
                        Runtime.import_bundle(bundle, target)
                self.assertFalse(target.exists())
                self.assertEqual(blob.read_bytes(), b"existing corrupt content")


if __name__ == "__main__":
    unittest.main()
