"""Offline trust lifecycle owns only protected host-side installation state."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import os
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


class TrustDocker:
    def __init__(self):
        self.running = False
        self.healthy = True
        self.recreated = False
        self.identifiers = {str(index + 1).zfill(64): service for index, service in enumerate(recovery.SERVICES)}

    def bind(self, state):
        if not (state / "daemon.json").exists():
            stack.write_protected(state / "daemon.json", {"format": 1, "endpoint": "unix:///fixture.sock", "daemonId": "fixture"})
        return self

    def require_stopped(self, project):
        if self.running:
            raise recovery.RecoveryError("community.recovery.down-required")

    def containers(self, project):
        return list(self.identifiers) if self.running else []

    def text(self, *args):
        assert args[0] == "inspect", args
        service = self.identifiers[args[-1]]
        if service in ("initialize", "migrate"):
            return f"{service}|exited|0|"
        health = "" if service == "otel-collector" else "healthy" if self.healthy else "unhealthy"
        return f"{service}|running|0|{health}"


class CommunityTrustTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.state = self.parent / "installation"
        stack.initialize(self.state, installation_inputs(), image="iip-community:test")
        self.original = {name: (self.state / name).read_bytes() for name in ("installation.json", "credentials.json")}
        self.old = trust.generation_digest(self.state / "transport")
        self.docker = TrustDocker()
        selected = patch.object(recovery, "RecoveryDocker", side_effect=self.docker.bind)
        selected.start()
        self.addCleanup(selected.stop)

    def activate(self):
        trust.prepare(self.state)
        trust.switch(self.state, "activate", external_trust_staged=True)

    def started(self):
        self.docker.running = True
        trust.record_started(self.state)

    def finalize(self):
        with patch.object(stack, "wait_for_collector"):
            trust.finalize(self.state, new_trust_verified=True)

    def test_prepare_is_idempotent_private_and_does_not_change_selected_material(self):
        trust.prepare(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        trust.prepare(self.state)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        status = trust.status(self.state)
        self.assertEqual(status["activeGeneration"], self.old)
        self.assertEqual(status["phase"], "prepared")
        self.assertFalse(status["expired"])
        self.assertFalse(status["expiringSoon"])
        overlap = Path(status["overlapCaPath"])
        self.assertEqual(overlap.read_bytes().count(b"BEGIN CERTIFICATE"), 2)
        self.assertEqual(overlap.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list((self.state / trust.GENERATIONS).iterdir())), 2)
        for name, content in self.original.items():
            self.assertEqual((self.state / name).read_bytes(), content)
        with self.assertRaisesRegex(trust.TrustError, "activation-or-cancel"):
            stack.installed_environment(self.state)

    def test_activation_requires_external_trust_acknowledgement_and_stopped_project(self):
        trust.prepare(self.state)
        with self.assertRaisesRegex(trust.TrustError, "acknowledgement"):
            trust.switch(self.state, "activate", external_trust_staged=False)
        self.docker.running = True
        with self.assertRaisesRegex(recovery.RecoveryError, "down-required"):
            trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual(trust.status(self.state)["activeGeneration"], self.old)

    def test_active_environment_selects_exact_generation_and_repeated_activation_does_not_write(self):
        self.activate()
        status = trust.status(self.state)
        self.assertNotEqual(status["activeGeneration"], self.old)
        environment, _ = stack.installed_environment(self.state)
        self.assertEqual(environment["IIP_COMMUNITY_TRANSPORT_DIRECTORY"], str(Path(status["caPath"]).parent))
        self.assertEqual(environment["IIP_COMMUNITY_TRANSPORT_GENERATION"], status["activeGeneration"])
        original = (self.state / trust.STATE_FILE).read_bytes()
        trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), original)
        for name, content in self.original.items():
            self.assertEqual((self.state / name).read_bytes(), content)

    def test_rollback_changes_trust_only_and_needs_a_fresh_healthy_start_before_finalize(self):
        self.activate()
        self.started()
        self.docker.running = False
        trust.switch(self.state, "rollback", external_trust_staged=True)
        self.assertEqual(trust.status(self.state)["activeGeneration"], self.old)
        with self.assertRaisesRegex(trust.TrustError, "successful-start-required"):
            self.finalize()
        self.started()
        self.finalize()
        self.assertEqual(trust.status(self.state)["phase"], "stable")
        self.assertIsNone(trust.status(self.state)["overlapCaPath"])
        self.finalize()  # No repeat mutation after an acknowledged completion.
        self.assertEqual(len(trust._document(self.state)["history"]), 4)
        for name, content in self.original.items():
            self.assertEqual((self.state / name).read_bytes(), content)

    def test_finalize_requires_operator_intake_assertion_and_same_healthy_running_containers(self):
        self.activate()
        self.started()
        with self.assertRaisesRegex(trust.TrustError, "verification-required"):
            trust.finalize(self.state, new_trust_verified=False)
        self.docker.healthy = False
        with self.assertRaisesRegex(trust.TrustError, "unhealthy"):
            self.finalize()
        self.docker.healthy = True
        first = next(iter(self.docker.identifiers))
        self.docker.identifiers["f" * 64] = self.docker.identifiers.pop(first)
        with self.assertRaisesRegex(trust.TrustError, "successful-start-required"):
            self.finalize()
        trust.record_started(self.state)
        self.finalize()

    def test_cancel_only_prepared_keeps_historical_material_and_never_fixes_expiry(self):
        trust.prepare(self.state)
        trust.cancel(self.state)
        before = (self.state / trust.STATE_FILE).read_bytes()
        trust.cancel(self.state)
        self.assertEqual((self.state / trust.STATE_FILE).read_bytes(), before)
        self.assertEqual(trust.status(self.state)["activeGeneration"], self.old)
        self.assertEqual(len(list((self.state / trust.GENERATIONS).iterdir())), 2)
        stack.installed_environment(self.state)
        self.activate()
        with self.assertRaisesRegex(trust.TrustError, "phase-invalid"):
            trust.cancel(self.state)

    def test_pending_rotation_blocks_configuration_and_backup_before_docker_or_mutation(self):
        trust.prepare(self.state)
        with patch.object(stack, "run_compose") as compose:
            with self.assertRaisesRegex(trust.TrustError, "finalize-or-cancel"):
                stack.configure(self.state, installation_inputs())
            compose.assert_not_called()
        with patch.object(recovery, "RecoveryDocker") as docker:
            with self.assertRaisesRegex(trust.TrustError, "finalize-or-cancel"):
                recovery.backup(self.state, self.parent / "backup", self.parent / "key")
            docker.assert_not_called()

    def test_prepare_can_rescue_expired_source_but_rollback_cannot_reactivate_it(self):
        expired = self.parent / "expired"
        expired.mkdir(mode=0o700)
        with patch.object(transport, "datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) - timedelta(days=366)
            transport.write_transport(expired)
        for name in transport.TRANSPORT_FILES:
            (self.state / "transport" / name).write_bytes((expired / "transport" / name).read_bytes())
        self.assertTrue(trust.status(self.state)["expired"])
        self.assertTrue(trust.status(self.state)["expiringSoon"])
        self.activate()
        self.assertFalse(trust.status(self.state)["expired"])
        with self.assertRaisesRegex(ValueError, "invalid-or-expired"):
            trust.switch(self.state, "rollback", external_trust_staged=True)
        self.assertEqual(trust.status(self.state)["phase"], "activated")

    def test_changed_credentials_or_configuration_cannot_be_silently_activated(self):
        trust.prepare(self.state)
        credentials = stack.read_protected(self.state / "credentials.json")
        credentials["apiToken"] = "z" * 64
        trust._atomic(self.state / "credentials.json", credentials)
        with self.assertRaisesRegex(trust.TrustError, "source-changed"):
            trust.switch(self.state, "activate", external_trust_staged=True)

    def test_generation_overlap_and_state_symlinks_or_tampering_fail_closed(self):
        trust.prepare(self.state)
        overlap = Path(trust.status(self.state)["overlapCaPath"])
        original = overlap.read_bytes()
        overlap.write_bytes(original + b"private-tamper")
        with self.assertRaisesRegex(trust.TrustError, "overlap-invalid"):
            trust.switch(self.state, "activate", external_trust_staged=True)
        overlap.write_bytes(original)
        document = trust._document(self.state)
        certificate = self.state / trust.GENERATIONS / document["rotation"]["to"] / "ca.crt"
        certificate.unlink()
        certificate.symlink_to(self.state / "transport/ca.crt")
        with self.assertRaises((ValueError, OSError)):
            trust.switch(self.state, "activate", external_trust_staged=True)

    def test_pointer_must_reconcile_with_phase_history(self):
        self.activate()
        document = trust._document(self.state)
        document["rotation"]["phase"] = "rolled-back"
        document["active"] = document["rotation"]["from"]
        trust._atomic(self.state / trust.STATE_FILE, document)
        with self.assertRaisesRegex(trust.TrustError, "history-invalid"):
            trust.selected_transport(self.state)
        # Rescue shutdown does not parse a broken selection or stale certificate.
        stack.installed_environment(self.state, validate_contracts=False)

    def test_crash_before_initial_pointer_or_activation_keeps_one_complete_selection(self):
        with patch.object(trust, "_atomic", side_effect=OSError("fixture-crash")):
            with self.assertRaises(OSError):
                trust.prepare(self.state)
        self.assertFalse((self.state / trust.STATE_FILE).exists())
        self.assertEqual(trust.selected_transport(self.state)[1], self.old)
        trust.prepare(self.state)
        with patch.object(trust, "_atomic", side_effect=OSError("fixture-crash")):
            with self.assertRaises(OSError):
                trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertEqual(trust.status(self.state)["activeGeneration"], self.old)
        self.assertEqual(trust.status(self.state)["phase"], "prepared")
        trust.switch(self.state, "activate", external_trust_staged=True)
        self.assertNotEqual(trust.status(self.state)["activeGeneration"], self.old)

    def test_settled_generation_layout_survives_encrypted_restore_without_stale_start_receipts(self):
        from test_community_recovery import FakeDocker, snapshot
        import community_backup_crypto as crypto

        self.activate()
        self.started()
        self.finalize()
        expected = trust.status(self.state)["activeGeneration"]
        stack.write_protected(self.state / "runtime-images.json", snapshot())
        archive, key, destination = self.parent / "backup", self.parent / "key", self.parent / "restored"
        crypto.create_key(key)
        fake = FakeDocker(self.state)
        with patch.object(recovery, "RecoveryDocker", side_effect=fake.bind):
            recovery.backup(self.state, archive, key)
            recovery.restore(destination, archive, key, source_fenced=True)
        self.assertEqual(trust.status(destination)["activeGeneration"], expected)
        self.assertEqual(trust._document(destination), trust._document(self.state))
        self.assertFalse((destination / trust.STARTED).exists())
        self.assertEqual((destination / "credentials.json").read_bytes(), self.original["credentials.json"])

    def test_cli_failures_are_minimized_and_status_never_prints_private_keys(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = trust.main(["--state", str(self.state), "activate"])
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.state), error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())
        self.assertEqual(output.getvalue(), "")
        with redirect_stdout(output):
            self.assertEqual(trust.main(["--state", str(self.state), "status"]), 0)
        self.assertNotIn("PRIVATE KEY", output.getvalue())
        self.assertNotIn(stack.read_protected(self.state / "credentials.json")["apiToken"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
