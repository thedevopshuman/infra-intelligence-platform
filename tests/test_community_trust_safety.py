"""Crash, rescue, and exact-state fences for the offline trust lifecycle."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_recovery as recovery
import community_stack as stack
import community_transport as transport
import community_trust as trust
from test_community_stack import installation_inputs
from test_community_trust import TrustDocker


class CommunityTrustSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.state = self.parent / "installation"
        stack.initialize(self.state, installation_inputs(), image="iip-community:test")
        self.old = trust.generation_digest(self.state / "transport")
        self.docker = TrustDocker()
        selected = patch.object(recovery, "RecoveryDocker", side_effect=self.docker.bind)
        self.docker_factory = selected.start()
        self.addCleanup(selected.stop)

    def activate(self):
        trust.prepare(self.state)
        trust.switch(self.state, "activate", external_trust_staged=True)

    def test_cli_lock_contention_cannot_change_or_observe_a_half_transition(self):
        output, error = io.StringIO(), io.StringIO()
        with stack.installation_lock(self.state), redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(trust.main(["--state", str(self.state), "prepare"]), 2)
        self.docker_factory.assert_not_called()
        self.assertFalse((self.state / trust.STATE_FILE).exists())
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(str(self.state), error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_incomplete_recovery_blocks_prepare_before_daemon_binding(self):
        (self.state / ".recovery-incomplete").symlink_to(self.parent / "absent")
        with self.assertRaisesRegex(ValueError, "community.recovery.incomplete"):
            trust.prepare(self.state)
        self.docker_factory.assert_not_called()
        self.assertFalse((self.state / trust.STATE_FILE).exists())
        self.assertFalse((self.state / trust.GENERATIONS).exists())

    def test_incomplete_recovery_blocks_activation_and_finalization(self):
        trust.prepare(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        marker = self.state / ".recovery-incomplete"
        marker.touch(mode=0o600)
        with self.assertRaisesRegex(ValueError, "community.recovery.incomplete"):
            trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        marker.unlink()
        trust.switch(self.state, "activate", external_trust_staged=True)
        before = (self.state / trust.STATE_FILE).read_bytes()
        marker.touch(mode=0o600)
        with self.assertRaisesRegex(ValueError, "community.recovery.incomplete"):
            trust.finalize(self.state, new_trust_verified=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)

    def test_prepare_rechecks_stop_before_publishing_pointer(self):
        original = self.docker.require_stopped
        calls = 0

        def stopped(project):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.docker.running = True
            return original(project)

        with patch.object(self.docker, "require_stopped", side_effect=stopped):
            with self.assertRaisesRegex(ValueError, "down-required"):
                trust.prepare(self.state)
        self.assertEqual(calls, 2)
        self.assertFalse((self.state / trust.STATE_FILE).exists())
        self.assertEqual(trust.selected_transport(self.state)[1], self.old)
        self.docker.running = False
        trust.prepare(self.state)
        self.assertEqual(trust.status(self.state)["phase"], "prepared")

    def test_prepare_failure_after_pointer_rename_can_be_retried_without_new_keys(self):
        synchronize = stack._sync_directory

        def fail_final_sync(path):
            if path == self.state and (self.state / trust.STATE_FILE).exists():
                raise OSError("fixture directory fsync failure")
            synchronize(path)

        with patch.object(stack, "_sync_directory", side_effect=fail_final_sync):
            with self.assertRaises(OSError):
                trust.prepare(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        generations = set((self.state / trust.GENERATIONS).iterdir())
        self.assertEqual(trust.status(self.state)["phase"], "prepared")
        with patch.object(transport, "write_transport") as generate:
            trust.prepare(self.state)
        generate.assert_not_called()
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        self.assertEqual(set((self.state / trust.GENERATIONS).iterdir()), generations)

    def test_activation_failure_after_pointer_rename_is_a_complete_idempotent_transition(self):
        trust.prepare(self.state)
        document = trust._document(self.state)
        with patch.object(stack, "_sync_directory", side_effect=OSError("fixture directory fsync failure")):
            with self.assertRaises(OSError):
                trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual(trust.selected_transport(self.state)[1], document["rotation"]["to"])
        before = (self.state / trust.STATE_FILE).read_bytes()
        trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        self.assertEqual([event["action"] for event in trust._document(self.state)["history"]], ["prepare", "activate"])

    def test_bound_daemon_metadata_change_invalidates_pending_rotation(self):
        trust.prepare(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        daemon = stack.read_protected(self.state / "daemon.json")
        daemon["daemonId"] = "replacement-fixture-daemon"
        trust._atomic(self.state / "daemon.json", daemon)
        with self.assertRaisesRegex(trust.TrustError, "source-changed"):
            trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)

    def test_finalization_collector_failure_keeps_rotation_and_rollback_available(self):
        self.activate()
        self.docker.running = True
        trust.record_started(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        with patch.object(stack, "wait_for_collector", side_effect=ValueError("fixture readiness failure")):
            with self.assertRaises(ValueError):
                trust.finalize(self.state, new_trust_verified=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        self.docker.running = False
        trust.switch(self.state, "rollback", external_trust_staged=True)
        self.assertEqual(trust.selected_transport(self.state)[1], self.old)

    def test_missing_duplicate_and_unhealthy_runtime_never_create_success_receipt(self):
        self.activate()
        self.docker.running = True
        complete = dict(self.docker.identifiers)
        for variant in ("missing", "duplicate", "unhealthy"):
            with self.subTest(variant=variant):
                self.docker.identifiers = dict(complete)
                self.docker.healthy = True
                if variant == "missing":
                    self.docker.identifiers.pop(next(iter(complete)))
                elif variant == "duplicate":
                    self.docker.identifiers["f" * 64] = "api"
                else:
                    self.docker.healthy = False
                with self.assertRaises(trust.TrustError):
                    trust.record_started(self.state)
                self.assertFalse((self.state / trust.STARTED).exists())

    def test_cli_nested_document_and_daemon_errors_are_value_minimized(self):
        for failure in (RecursionError("private nested content"), OSError("private daemon socket")):
            output, error = io.StringIO(), io.StringIO()
            with patch.object(trust, "prepare", side_effect=failure), redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(trust.main(["--state", str(self.state), "prepare"]), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("private", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())
            self.assertNotIn(str(self.state), error.getvalue())


if __name__ == "__main__":
    unittest.main()
