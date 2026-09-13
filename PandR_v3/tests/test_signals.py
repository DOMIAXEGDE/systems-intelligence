"""Durable communication between distinct JSON sessions, including retry edges."""
import copy
from pathlib import Path
import tempfile
import unittest

from pandr.canonical import digest
from pandr.errors import BudgetExceeded, ContractError, RequestIdConflict, RevisionConflict, ValidationError
from pandr.runtime import Runtime


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pandr-signals-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sender = Runtime.create(self.root / "producer" / "session.json")
        self.receiver = Runtime.create(self.root / "consumer" / "session.json")

    def enqueue(self, ranks, **options):
        receipt = self.sender.commit(self.sender.plan().send(self.receiver.path, ranks, **options))
        return copy.deepcopy(receipt["outputs"][0]["data"])

    def test_resolved_ranks_route_to_receiver_and_generate_persisted_text(self):
        self.receiver.commit(self.receiver.plan().configure_layer("numeric", {"config": {"on_rank_stream": ["text"]}}))
        self.sender.commit(self.sender.plan().connect("sequence", self.receiver.path).resolve("v17 v7 v3 v23 h37 v23"))
        result = self.sender.drain_outbox()
        self.assertEqual([r["status"] for r in result], ["acknowledged"])
        receiver = Runtime.open(self.receiver.path)
        state = receiver.snapshot()
        self.assertEqual(state["domain"]["last_ranks"], ["17", "7", "3", "23", "37", "23"])
        artifact = next(iter(state["artifacts"].values()))
        self.assertEqual((receiver.path.parent / artifact["path"]).read_text(encoding="utf-8"), "0&\"6D6")
        self.assertEqual(len(state["delivery_ledger"]), 1)
        revision = receiver.revision
        self.assertEqual(self.sender.drain_outbox(), [])
        self.assertEqual(Runtime.open(receiver.path).revision, revision)

    def test_order_rejection_then_predecessor_and_duplicate_delivery(self):
        first = self.enqueue(["17"])
        second = self.enqueue(["7"])
        before = self.receiver.snapshot()
        with self.assertRaisesRegex(ValidationError, "out-of-order"):
            self.receiver.receive(second)
        self.assertEqual(self.receiver.snapshot(), before)
        receipt = self.receiver.receive(first)
        revision = self.receiver.revision
        self.assertEqual(self.receiver.receive(first), receipt)
        self.assertEqual(self.receiver.revision, revision)
        self.receiver.receive(second)
        state = self.receiver.snapshot()
        self.assertEqual(state["domain"]["last_ranks"], ["7"])
        self.assertEqual([e["sequence"] for e in state["domain"]["received"]], [0, 1])
        self.assertEqual(len(state["inbox"]), 2)

    def test_acknowledgement_loss_retries_same_message_after_transport_status_change(self):
        envelope = self.enqueue(["17"])
        original = self.receiver.receive(envelope)
        self.sender.repository.metadata(lambda state: state["outbox"][0].update(status="failed", delivery_detail="acknowledgement lost"))
        results = self.sender.drain_outbox()
        self.assertEqual(results[0]["status"], "acknowledged", results)
        self.receiver.reload()
        self.assertEqual(len(self.receiver.snapshot()["inbox"]), 1)
        self.assertEqual(self.receiver.snapshot()["receipts"][-1]["digest"], original["digest"])

    def test_message_id_reuse_with_altered_payload_is_rejected(self):
        envelope = self.enqueue(["17"])
        self.receiver.receive(envelope)
        before = self.receiver.path.read_bytes()
        changed = copy.deepcopy(envelope)
        changed["payload"] = ["7"]
        changed["payload_digest"] = digest(changed["payload"])
        with self.assertRaises((RequestIdConflict, ValidationError)):
            self.receiver.receive(changed)
        self.assertEqual(self.receiver.path.read_bytes(), before)

    def test_payload_hash_mismatch_revision_precondition_and_disabled_destination(self):
        envelope = self.enqueue(["17"], expected_revision=0)
        damaged = copy.deepcopy(envelope)
        damaged["payload"] = ["7"]
        with self.assertRaises(ValidationError):
            self.receiver.receive(damaged)
        self.receiver.commit(self.receiver.plan().set_notes("advance revision"))
        with self.assertRaises(RevisionConflict):
            self.receiver.receive(envelope)
        fresh = self.enqueue(["3"])
        self.receiver.commit(self.receiver.plan().configure_layer("numeric", {"enabled": False}))
        with self.assertRaises(ContractError):
            self.receiver.receive(fresh)
        self.assertEqual(self.receiver.snapshot()["inbox"], [])

    def test_envelope_addressed_to_another_session_is_rejected(self):
        envelope = self.enqueue(["17"])
        stranger = Runtime.create(self.root / "third" / "session.json")
        with self.assertRaises(ValidationError):
            stranger.receive(envelope)
        self.assertEqual(stranger.revision, 0)

    def test_send_hop_and_retained_queue_limits_are_atomic(self):
        with self.assertRaises(BudgetExceeded):
            self.enqueue(["17"], hop_count=2, max_hops=2)
        self.assertEqual(self.sender.revision, 0)
        self.sender.commit(self.sender.plan().set_budgets(max_queue=1))
        self.enqueue(["17"])
        before = self.sender.path.read_bytes()
        with self.assertRaises(BudgetExceeded):
            self.enqueue(["7"])
        self.assertEqual(self.sender.path.read_bytes(), before)

    def test_signal_replay_verifies_without_live_delivery(self):
        self.enqueue(["17"])
        self.sender.drain_outbox()
        before = self.receiver.path.read_bytes()
        result = self.sender.replay(self.root / "replay" / "session.json")
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["live_delivery"])
        self.assertEqual(self.receiver.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
