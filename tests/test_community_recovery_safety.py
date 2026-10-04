"""Adversarial checks for the operational cold-recovery boundary only."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_recovery as recovery
import community_stack as stack


class RecoveryHelperSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docker = recovery.RecoveryDocker.__new__(recovery.RecoveryDocker)
        self.docker.prefix = ["/trusted/docker", "--host", "unix:///local.sock"]
        self.docker.environment = {"PATH": "/trusted"}
        self.name = "iip-recovery-" + "a" * 24
        selected = patch.object(recovery.secrets, "token_hex", return_value="a" * 24)
        selected.start()
        self.addCleanup(selected.stop)

    def helper(self, **kwargs):
        return self.docker.helper(
            "sha256:" + "1" * 64,
            ["type=volume,src=exact-owned-volume,dst=/volume,readonly,volume-nocopy"],
            ["python", "/recovery.py", "export"],
            **kwargs,
        )

    def test_timeout_removes_only_exact_allocated_helper_and_proves_absence(self) -> None:
        with (
            patch.object(recovery.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                "private-command", 900, output=b"private-output", stderr=b"private-secret"
            )),
            patch.object(self.docker, "text", side_effect=["f" * 64, "", ""]) as text,
        ):
            with self.assertRaisesRegex(recovery.RecoveryError, "helper-failed") as raised:
                self.helper()
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual([call.args for call in text.call_args_list], [
            ("ps", "--all", "--quiet", "--filter", f"name=^{self.name}$"),
            ("rm", "--force", self.name),
            ("ps", "--all", "--quiet", "--filter", f"name=^{self.name}$"),
        ])

    def test_successful_command_still_fails_if_helper_cannot_be_removed(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout=b"success")
        with (
            patch.object(recovery.subprocess, "run", return_value=result),
            patch.object(self.docker, "text", side_effect=["f" * 64, "", "f" * 64]),
        ):
            with self.assertRaisesRegex(recovery.RecoveryError, "helper-cleanup-required"):
                self.helper()

    def test_unknown_cleanup_state_never_counts_as_success(self) -> None:
        for responses in (
            [recovery.RecoveryError("community.recovery.docker-command-failed")],
            ["f" * 64, recovery.RecoveryError("community.recovery.docker-command-failed")],
        ):
            with (
                self.subTest(responses=len(responses)),
                patch.object(recovery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"done")),
                patch.object(self.docker, "text", side_effect=responses),
            ):
                with self.assertRaises(recovery.RecoveryError):
                    self.helper()

    def test_helper_is_offline_nonpulling_and_does_not_inherit_proxy_credentials(self) -> None:
        with (
            patch.object(recovery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"done")) as run,
            patch.object(self.docker, "text", return_value="") as text,
        ):
            self.assertEqual(self.helper(), "done")
        command = run.call_args.args[0]
        for first, second in (
            ("--pull", "never"), ("--network", "none"), ("--user", "0:0"),
            ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges:true"),
            ("--pids-limit", "64"), ("--memory", "256m"),
        ):
            self.assertEqual(command[command.index(first) + 1], second)
        self.assertIn("--read-only", command)
        self.assertIn("io.iip.community-recovery-helper=true", command)
        self.assertIn("LC_ALL=C", command)
        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
                         "http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            self.assertIn(variable + "=", command)
        self.assertIn("NO_PROXY=*", command)
        self.assertIn("no_proxy=*", command)
        self.assertNotIn("--privileged", command)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(text.call_count, 1)

    def test_postgres_inspection_gets_no_root_capability_additions(self) -> None:
        with (
            patch.object(recovery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"done")) as run,
            patch.object(self.docker, "text", return_value=""),
        ):
            self.helper(user="70:70")
        self.assertNotIn("--cap-add", run.call_args.args[0])

    def test_invalid_mount_is_rejected_before_container_creation(self) -> None:
        with patch.object(recovery.subprocess, "run") as run, patch.object(self.docker, "text") as text:
            with self.assertRaisesRegex(recovery.RecoveryError, "mount-invalid"):
                self.docker.helper("sha256:" + "1" * 64, ["bad\nmount"], ["python"])
        run.assert_not_called()
        text.assert_not_called()


class RecoveryCliSafetyTests(unittest.TestCase):
    def test_operational_failures_emit_only_stable_minimized_error(self) -> None:
        failures = (
            ValueError("private-key-and-price"),
            OSError("private-directory"),
            TypeError("private-profile"),
            KeyError("private-tenant"),
            tarfile.ReadError("private-archive"),
            RecursionError("private-nested-manifest"),
        )
        for command in ("backup", "restore"):
            for failure in failures:
                with self.subTest(command=command, failure=type(failure).__name__):
                    output, error = io.StringIO(), io.StringIO()
                    with (
                        patch.object(recovery, command, side_effect=failure),
                        patch.object(stack, "installation_lock", return_value=nullcontext()),
                        redirect_stdout(output), redirect_stderr(error),
                    ):
                        code = recovery.main([
                            command, "--state", "/protected/private-installation",
                            "--archive", "/protected/private-archive",
                            "--key", "/protected/private-key",
                        ])
                    self.assertEqual(code, 2)
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn("ERROR: community.recovery.failed;", error.getvalue())
                    self.assertNotIn("private-", error.getvalue())
                    self.assertNotIn("Traceback", error.getvalue())

    def test_key_generation_failure_never_prints_key_path_or_value(self) -> None:
        output, error = io.StringIO(), io.StringIO()
        with (
            patch("community_backup_crypto.create_key", side_effect=ValueError("private-key-value")),
            redirect_stdout(output), redirect_stderr(error),
        ):
            code = recovery.main(["keygen", "--key", "/protected/private-key"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("private-key", error.getvalue())


class RecoveryOuterArchiveSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.payload = self.parent / "payload.tar"
        self.work = self.parent / "unpacked"
        self.work.mkdir(mode=0o700)
        self.project = "iip-community-" + "a" * 10

    def archive(self, *, format=tarfile.USTAR_FORMAT, extra=None, mutate_manifest=None, pax=None) -> bytes:
        contents = {"state/installation.json": stack.compact({"project": self.project}).encode()}
        contents.update({name + ".tar": b"volume" for name in recovery.VOLUMES})
        if extra:
            contents.update(extra)
        manifest = {
            "format": "iip-community-backup-v1", "createdAt": "2026-10-04T06:00:00+00:00",
            "sourceProject": self.project,
            "sourceDaemon": {"format": 1, "endpoint": "unix:///fixture.sock", "daemonId": "fixture-daemon"},
            "runtime": {},
            "members": {name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                        for name, value in contents.items()},
        }
        if mutate_manifest:
            mutate_manifest(manifest)
        contents = {"manifest.json": stack.compact(manifest).encode(), **contents}
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=format) as archive:
            for name, value in contents.items():
                entry = tarfile.TarInfo(name)
                entry.mode, entry.size = 0o600, len(value)
                if pax:
                    entry.pax_headers = pax
                archive.addfile(entry, io.BytesIO(value))
        return output.getvalue()

    def unpack(self, value: bytes) -> dict:
        self.payload.write_bytes(value)
        self.payload.chmod(0o600)
        # Isolate the envelope grammar from separately tested state, volume,
        # and runtime contracts. A valid envelope must reach those boundaries.
        with ExitStack() as selected:
            selected.enter_context(patch.object(recovery, "validate_snapshot"))
            selected.enter_context(patch.object(recovery, "_state_paths", return_value=["installation.json"]))
            selected.enter_context(patch.object(recovery, "_volume_module", return_value=Mock()))
            selected.enter_context(patch.object(stack, "installed_environment", return_value=({}, self.project)))
            return recovery._unpack(self.payload, self.work, 1024 * 1024)

    def test_valid_canonical_outer_envelope_reaches_structural_validation(self) -> None:
        self.assertEqual(self.unpack(self.archive())["sourceProject"], self.project)

    def test_gnu_headers_are_not_silently_accepted_as_ustar(self) -> None:
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(self.archive(format=tarfile.GNU_FORMAT))

    def test_pax_extensions_are_rejected(self) -> None:
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(self.archive(format=tarfile.PAX_FORMAT, pax={"comment": "not-supported"}))

    def test_nonzero_bytes_after_archive_terminator_are_rejected(self) -> None:
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(self.archive() + b"hidden-nonzero-content")

    def test_nonzero_member_padding_is_rejected(self) -> None:
        value = bytearray(self.archive())
        with tarfile.open(fileobj=io.BytesIO(value), mode="r:") as archive:
            member = archive.next()
            self.assertIsNotNone(member)
            padding = member.offset_data + member.size
            self.assertNotEqual(padding % 512, 0)
        value[padding] = 1
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(bytes(value))

    def test_only_one_terminating_zero_block_is_rejected(self) -> None:
        value = self.archive()
        with tarfile.open(fileobj=io.BytesIO(value), mode="r:") as archive:
            last = archive.getmembers()[-1]
            end = last.offset_data + ((last.size + 511) // 512) * 512
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(value[:end + 512])

    def test_manifest_cannot_name_a_member_outside_the_actual_archive(self) -> None:
        def mutate(manifest):
            manifest["members"]["state/../outside"] = {"bytes": 0, "sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "manifest-invalid"):
            self.unpack(self.archive(mutate_manifest=mutate))
        self.assertFalse((self.parent / "outside").exists())

    def test_state_member_traversal_is_rejected_before_writing_outside_work(self) -> None:
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(self.archive(extra={"state/../../outside": b"forbidden"}))
        self.assertFalse((self.parent / "outside").exists())

    def test_noncanonical_member_path_is_rejected(self) -> None:
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.unpack(self.archive(extra={"state//extra.json": b"{}"}))


class RecoverySourceAndFenceSafetyTests(unittest.TestCase):
    def test_source_transport_and_config_roots_cannot_be_symlinked(self) -> None:
        from tests.test_community_stack import installation_inputs

        for name in ("transport", "config"):
            with self.subTest(directory=name), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                state = parent / "source"
                stack.initialize(state, installation_inputs(), image="iip-community:test")
                outside = parent / "outside"
                (state / name).rename(outside)
                (state / name).symlink_to(outside, target_is_directory=True)

                with self.assertRaisesRegex(stack.InstallationError, "protection-required"):
                    recovery._state_paths(state)
                self.assertTrue(outside.is_dir())

    def test_restore_syncs_fence_and_parent_before_first_state_move(self) -> None:
        from tests.test_community_recovery import FakeDocker, snapshot
        from tests.test_community_stack import installation_inputs
        import community_backup_crypto as crypto

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            source, destination = parent / "source", parent / "restored"
            key, archive = parent / "key.bin", parent / "backup.iipbak"
            stack.initialize(source, installation_inputs(), image="iip-community:test")
            stack.write_protected(source / "runtime-images.json", snapshot())
            crypto.create_key(key)
            docker = FakeDocker(source)
            with patch.object(recovery, "RecoveryDocker", side_effect=docker.bind):
                with stack.installation_lock(source):
                    recovery.backup(source, archive, key)

                events = []
                original_sync = stack._sync_directory
                original_rename = os.rename

                def sync(path):
                    original_sync(path)
                    if (destination / ".recovery-incomplete").is_file():
                        events.append(("fence-sync", Path(path)))

                def rename(old, new):
                    target = Path(new)
                    if target.parent == destination:
                        self.assertTrue((destination / ".recovery-incomplete").is_file())
                        self.assertIn(("fence-sync", destination), events)
                        self.assertIn(("fence-sync", parent), events)
                        events.append(("state-move", target))
                    return original_rename(old, new)

                with patch.object(stack, "_sync_directory", side_effect=sync), patch.object(os, "rename", side_effect=rename):
                    recovery.restore(destination, archive, key, source_fenced=True)

            self.assertTrue(any(kind == "state-move" for kind, _ in events))
            self.assertFalse((destination / ".recovery-incomplete").exists())


if __name__ == "__main__":
    unittest.main()
