"""Atomicity, concurrent writers, recovery, integrity and request retries."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from pandr.canonical import canonical
from pandr.errors import RequestIdConflict, RevisionConflict, UnsupportedSchema, ValidationError
from pandr.repository import Repository, seal
from pandr.runtime import Runtime


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pandr-repository-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = Runtime.create(self.root / "session.json")

    def test_stale_writer_never_overwrites_committed_changes(self):
        second = Runtime.open(self.runtime.path)
        stale = second.plan().set_function("x+99")
        self.runtime.commit(self.runtime.plan().set_function("x+1"))
        expected = self.runtime.path.read_bytes()
        with self.assertRaises(RevisionConflict):
            second.commit(stale)
        self.assertEqual(self.runtime.path.read_bytes(), expected)
        second.reload()
        self.assertEqual(second.snapshot(), self.runtime.snapshot())

    def test_concurrent_writers_commit_exactly_once_from_same_revision(self):
        barrier = Barrier(2)
        writers = [Runtime.open(self.runtime.path), Runtime.open(self.runtime.path)]
        plans = [runtime.plan().set_notes(f"writer-{index}") for index, runtime in enumerate(writers)]

        def attempt(index):
            barrier.wait(timeout=10)
            try:
                return writers[index].commit(plans[index])["status"]
            except RevisionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertCountEqual(results, ["committed", "conflict"])
        state = self.runtime.reload()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(len(state["receipts"]), 1)
        self.assertEqual(len(state["request_ledger"]), 1)
        self.assertIn(state["memory"]["workspace_notes"], {"writer-0", "writer-1"})
        self.assertEqual(self.runtime.validate()["status"], "valid")

    def test_same_revision_valid_external_edit_conflicts_with_old_plan(self):
        stale = self.runtime.plan().set_function("x+1")
        edited = copy.deepcopy(self.runtime.snapshot())
        edited["memory"]["workspace_notes"] = "external editor"
        seal(edited)
        self.runtime.path.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaises(RevisionConflict):
            self.runtime.commit(stale)
        self.assertEqual(Runtime.open(self.runtime.path).snapshot()["memory"]["workspace_notes"], "external editor")

    def test_same_revision_unsealed_tamper_is_rejected_without_backup(self):
        edited = copy.deepcopy(self.runtime.snapshot())
        edited["memory"]["workspace_notes"] = "tamper"
        self.runtime.path.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaises(ValidationError):
            Repository(self.runtime.path).load(recover=False)

    def test_successful_request_retry_returns_original_receipt_without_rerendering(self):
        plan = self.runtime.plan(request_id="once").resolve("v17").render("text")
        receipt = self.runtime.commit(plan)
        self.runtime.commit(self.runtime.plan().set_function("x+999"))
        before = self.runtime.path.read_bytes()
        with patch("pandr.artifacts.render_artifact", side_effect=AssertionError("must not render twice")):
            retry = self.runtime.commit(plan)
        self.assertEqual(retry, receipt)
        self.assertEqual(self.runtime.path.read_bytes(), before)
        self.assertEqual(self.runtime.revision, 2)
        with self.assertRaises(RequestIdConflict):
            self.runtime.commit(self.runtime.plan(request_id="once").set_notes("different command"))

    def test_atomic_replace_failure_leaves_previous_json_and_no_temporary_file(self):
        before = self.runtime.path.read_bytes()
        snapshot = self.runtime.snapshot()
        real_replace = os.replace

        def fail_main_replace(source, destination):
            if Path(destination) == self.runtime.path:
                raise OSError("simulated interrupted replacement")
            return real_replace(source, destination)

        with patch("pandr.repository.os.replace", side_effect=fail_main_replace):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.runtime.commit(self.runtime.plan().set_notes("not committed"))
        self.assertEqual(self.runtime.path.read_bytes(), before)
        self.assertEqual(self.runtime.snapshot(), snapshot)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(Runtime.open(self.runtime.path).validate()["status"], "valid")

    def test_corrupt_primary_restores_last_valid_backup_and_quarantines_original(self):
        self.runtime.commit(self.runtime.plan().set_notes("backup version"))
        backup_state = self.runtime.snapshot()
        self.runtime.commit(self.runtime.plan().set_notes("latest version"))
        corrupt = b'{"incomplete":'
        self.runtime.path.write_bytes(corrupt)
        restored = Runtime.open(self.runtime.path)
        self.assertEqual(restored.snapshot()["memory"], backup_state["memory"])
        self.assertEqual(restored.snapshot()["domain"], backup_state["domain"])
        self.assertEqual(restored.revision, backup_state["revision"] + 1)
        quarantine = self.root / restored.snapshot()["ui"]["recovery"]["quarantine"]
        self.assertEqual(quarantine.read_bytes(), corrupt)
        self.assertEqual(restored.validate()["status"], "valid")

    def test_unknown_schema_does_not_silently_restore_older_backup(self):
        self.runtime.commit(self.runtime.plan().set_notes("create backup"))
        state = self.runtime.snapshot()
        state["schema_version"] = 3
        encoded = json.dumps(state)
        self.runtime.path.write_text(encoded, encoding="utf-8")
        with self.assertRaises(UnsupportedSchema):
            Runtime.open(self.runtime.path)
        self.assertEqual(self.runtime.path.read_text(encoding="utf-8"), encoded)

    def test_size_limit_applies_to_actual_saved_json_bytes(self):
        candidate = self.runtime.snapshot()
        candidate["ui"]["many"] = [""] * 10000
        seal(candidate)
        compact = len(canonical(candidate))
        formatted = len(json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) + 1
        limit = (compact + formatted) // 2
        self.assertLess(self.runtime.path.stat().st_size, limit)
        before = self.runtime.path.read_bytes()
        with patch("pandr.repository.MAX_SESSION_BYTES", limit):
            with self.assertRaises(ValidationError):
                self.runtime.save_ui({"many": [""] * 10000})
            self.assertEqual(self.runtime.path.read_bytes(), before)
            self.assertEqual(self.runtime.repository.load(recover=False)["revision"], 0)


if __name__ == "__main__":
    unittest.main()
