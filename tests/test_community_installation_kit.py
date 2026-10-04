from __future__ import annotations

import gzip
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import installation_kit as kit


VERSION = "0.84.2"
PREFIX = f"infra-intelligence-community-{VERSION}"


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout


def commit(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "-c", "user.name=Kit Test", "-c", "user.email=kit@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "commit", "-m", "Test installation source")


def archive_bytes(
    *, omit: str | None = None, project: bytes | None = None,
    extra: tuple[tarfile.TarInfo, bytes] | None = None,
    archive_format: int = tarfile.USTAR_FORMAT,
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=archive_format) as archive:
        for name in sorted(kit.REQUIRED_KIT_PATHS):
            if name == omit:
                continue
            value = b"fixture\n"
            if name == "pyproject.toml":
                value = project if project is not None else f'[project]\nversion = "{VERSION}"\n'.encode()
            member = tarfile.TarInfo(f"{PREFIX}/{name}")
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
        if extra:
            member, value = extra
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return buffer.getvalue()


class CommunityInstallationKitTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.archive = self.directory / "kit.tar.gz"

    def inspect(self, body: bytes) -> dict:
        self.archive.write_bytes(body)
        return dict(kit.inspect_installation_kit(self.archive, VERSION))

    def source(self) -> Path:
        root = self.directory / "source"
        root.mkdir()
        git(root, "init", "-q")
        for name in kit.REQUIRED_KIT_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'[project]\nversion = "{VERSION}"\n' if name == "pyproject.toml" else "fixture\n")
        (root / ".gitignore").write_text(".iip/\n.env\nnode_modules/\n__pycache__/\n")
        commit(root)
        return root

    def test_minimum_inventory_tracks_operational_helpers_and_runtime_assets(self) -> None:
        paths = [
            *ROOT.glob("scripts/community_*.py"),
            *ROOT.glob("deploy/community/*"),
            *ROOT.glob("src/iip/adapters/postgres/migrations/*.sql"),
            *ROOT.glob("src/iip/surfaces/static/*"),
        ]
        expected = {str(path.relative_to(ROOT)) for path in paths if path.is_file()}
        self.assertTrue(expected.issubset(kit.REQUIRED_KIT_PATHS), expected - kit.REQUIRED_KIT_PATHS)

    def test_required_files_and_version_are_validated(self) -> None:
        result = self.inspect(archive_bytes())
        self.assertEqual(result["files"], len(kit.REQUIRED_KIT_PATHS))
        self.assertGreater(result["contentBytes"], 0)
        for missing in (
            "scripts/community_trust.py", "scripts/community_recovery.py", "scripts/community_images.py",
            "deploy/community/collector.yaml", "LICENSE",
            "src/iip/adapters/postgres/migrations/0026_ai_history_availability.sql",
        ):
            with self.subTest(missing=missing), self.assertRaisesRegex(kit.InstallationKitError, "incomplete"):
                self.inspect(archive_bytes(omit=missing))
        for project in (b"invalid = {", b'[project]\nversion="9.0.0"', b'project="invalid"'):
            with self.subTest(project=project), self.assertRaises(kit.InstallationKitError):
                self.inspect(archive_bytes(project=project))

    def test_rejects_unsafe_paths_and_duplicates(self) -> None:
        for name in (
            f"{PREFIX}/../escape", f"/{PREFIX}/scripts/extra.py",
            f"{PREFIX}/scripts/./extra.py", f"{PREFIX}/scripts//extra.py",
            f"{PREFIX}/scripts\\extra.py", f"{PREFIX}/.iip/credentials.json",
            f"{PREFIX}/scripts/.env", f"{PREFIX}/scripts/node_modules/secret.js",
            f"{PREFIX}/scripts/__pycache__/code.pyc", f"{PREFIX}/README.md",
            "different-version/README.md", PREFIX, f"{PREFIX}/scripts/secret.key",
        ):
            with self.subTest(name=name), self.assertRaises(kit.InstallationKitError):
                self.inspect(archive_bytes(extra=(tarfile.TarInfo(name), b"private sentinel")))

    def test_rejects_links_special_sparse_and_extended_metadata(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            member = tarfile.TarInfo(f"{PREFIX}/scripts/extra.py")
            member.type = kind
            member.linkname = "../escape" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            with self.subTest(kind=kind), self.assertRaises(kit.InstallationKitError):
                self.inspect(archive_bytes(extra=(member, b"")))
        for metadata in ({"comment": "untrusted metadata"}, {"GNU.sparse.map": "0,1"}):
            member = tarfile.TarInfo(f"{PREFIX}/scripts/extra.py")
            member.pax_headers = metadata
            with self.subTest(metadata=metadata), self.assertRaises(kit.InstallationKitError):
                self.inspect(archive_bytes(extra=(member, b"x"), archive_format=tarfile.PAX_FORMAT))
        member = tarfile.TarInfo(f"{PREFIX}/scripts/extra.py")
        member.mode = 0o4755
        with self.assertRaises(kit.InstallationKitError):
            self.inspect(archive_bytes(extra=(member, b"x")))

    def test_rejects_file_parents_in_both_archive_orders(self) -> None:
        # The ordinary required child entries arrive first, then this impostor.
        with self.assertRaises(kit.InstallationKitError):
            self.inspect(archive_bytes(extra=(tarfile.TarInfo(f"{PREFIX}/scripts"), b"x")))
        original = gzip.decompress(archive_bytes())
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            member = tarfile.TarInfo(f"{PREFIX}/scripts")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        # First non-directory parent followed immediately by original records.
        with self.assertRaises(kit.InstallationKitError):
            self.inspect(gzip.compress(buffer.getvalue()[:1024] + original))

    def test_bounded_stream_members_content_and_compressed_file(self) -> None:
        body = archive_bytes()
        for limit, value in (
            ("MAX_MEMBERS", 1), ("MAX_CONTENT_BYTES", 1), ("MAX_MEMBER_BYTES", 1),
            ("MAX_STREAM_BYTES", 1024), ("MAX_ARCHIVE_BYTES", len(body) - 1),
            ("MAX_PROJECT_BYTES", 1),
        ):
            with self.subTest(limit=limit), patch.object(kit, limit, value), self.assertRaises(kit.InstallationKitError):
                self.inspect(body)

    def test_truncated_corrupt_and_hidden_trailing_data_rejected(self) -> None:
        body = archive_bytes()
        corrupted = body[:-8] + bytes([body[-8] ^ 255]) + body[-7:]
        for invalid in (body[:-1], body[:100], b"not an archive", corrupted,
                        gzip.compress(gzip.decompress(body) + b"hidden archive payload")):
            with self.subTest(length=len(invalid)), self.assertRaises(kit.InstallationKitError):
                self.inspect(invalid)
        self.archive.write_bytes(body)
        linked = self.directory / "link.tar.gz"
        linked.symlink_to(self.archive)
        with self.assertRaises(kit.InstallationKitError):
            kit.inspect_installation_kit(linked, VERSION)

    def test_builder_is_reproducible_and_excludes_ignored_state(self) -> None:
        root = self.source()
        protected = root / ".iip"
        protected.mkdir()
        (protected / "credentials.json").write_text("operator-secret-must-not-package")
        (root / ".env").write_text("provider-secret-must-not-package")
        kit.build_installation_kit(root, self.archive, VERSION)
        second = self.directory / "second.tar.gz"
        kit.build_installation_kit(root, second, VERSION)
        self.assertEqual(self.archive.read_bytes(), second.read_bytes())
        unpacked = gzip.decompress(self.archive.read_bytes())
        self.assertNotIn(b"operator-secret-must-not-package", unpacked)
        self.assertNotIn(b"provider-secret-must-not-package", unpacked)
        with tarfile.open(self.archive) as archive:
            self.assertNotIn(f"{PREFIX}/.git", archive.getnames())
        self.assertEqual(git(root, "status", "--porcelain"), b"")

    def test_builder_rejects_dirty_untracked_incomplete_and_wrong_version(self) -> None:
        root = self.source()
        readme = root / "README.md"
        readme.write_text("dirty")
        with self.assertRaisesRegex(kit.InstallationKitError, "clean-required"):
            kit.build_installation_kit(root, self.archive, VERSION)
        readme.write_text("fixture\n")
        stray = root / "untracked.txt"
        stray.write_text("not committed")
        with self.assertRaisesRegex(kit.InstallationKitError, "clean-required"):
            kit.build_installation_kit(root, self.archive, VERSION)
        stray.unlink()
        with self.assertRaisesRegex(kit.InstallationKitError, "mismatch"):
            kit.build_installation_kit(root, self.archive, "0.99.0")
        readme.unlink()
        commit(root)
        with self.assertRaisesRegex(kit.InstallationKitError, "incomplete"):
            kit.build_installation_kit(root, self.archive, VERSION)
        self.assertFalse(self.archive.exists())

    def test_builder_rejects_committed_links_and_operator_paths(self) -> None:
        root = self.source()
        bad = root / "scripts" / "extra.py"
        bad.symlink_to("community_stack.py")
        commit(root)
        with self.assertRaisesRegex(kit.InstallationKitError, "path-invalid"):
            kit.build_installation_kit(root, self.archive, VERSION)
        bad.unlink()
        bad = root / "scripts" / ".env"
        bad.write_text("private sentinel")
        git(root, "add", "-f", "scripts/.env")
        commit(root)
        with self.assertRaisesRegex(kit.InstallationKitError, "path-invalid"):
            kit.build_installation_kit(root, self.archive, VERSION)

    def test_no_overwrite_even_symlink_and_failure_does_not_publish(self) -> None:
        root = self.source()
        self.archive.write_bytes(b"keep")
        with self.assertRaisesRegex(kit.InstallationKitError, "output.exists"):
            kit.build_installation_kit(root, self.archive, VERSION)
        self.assertEqual(self.archive.read_bytes(), b"keep")
        self.archive.unlink()
        self.archive.symlink_to(self.directory / "missing")
        with self.assertRaisesRegex(kit.InstallationKitError, "output.exists"):
            kit.build_installation_kit(root, self.archive, VERSION)
        self.assertFalse((self.directory / "missing").exists())

    def test_source_change_during_build_does_not_publish(self) -> None:
        root = self.source()
        real_check = kit._clean_revision
        revision = real_check(root)
        with patch.object(kit, "_clean_revision", side_effect=[revision, "0" * 40]):
            with self.assertRaisesRegex(kit.InstallationKitError, "source.changed"):
                kit.build_installation_kit(root, self.archive, VERSION)
        self.assertFalse(self.archive.exists())

    def test_cli_errors_contain_no_path_or_untrusted_archive_text(self) -> None:
        self.archive.write_bytes(b"private sentinel")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/installation_kit.py"), "inspect",
             str(self.archive), "--version", VERSION], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "installation-kit.archive.invalid")
        for version in ("../bad", "", "1.2.3/evil", "0" * 65):
            with self.subTest(version=version), self.assertRaises(kit.InstallationKitError):
                kit.inspect_installation_kit(self.archive, version)

    def test_real_committed_kit_runs_without_checkout_or_inherited_pythonpath(self) -> None:
        from tests.test_community_stack import installation_inputs

        # A genuine clean Git snapshot: overlay the current minimum operating
        # closure before committing this owned test tree, so a new installer
        # helper or changed startup path is exercised before the root commit.
        source = self.directory / "snapshot"
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(git(ROOT, "archive", "--format=tar", "HEAD"))) as archive:
            for member in archive:
                target = source / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(member)
                    self.assertIsNotNone(stream)
                    with stream:
                        target.write_bytes(stream.read())
                    target.chmod(member.mode & 0o777)
                else:
                    self.fail("Source snapshot unexpectedly contains a link or special entry")
        for name in kit.REQUIRED_KIT_PATHS:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        git(source, "init", "-q")
        commit(source)
        kit.build_installation_kit(source, self.archive, VERSION)
        kit.inspect_installation_kit(self.archive, VERSION)
        unpack = self.directory / "unpacked"
        unpack.mkdir()
        with tarfile.open(self.archive) as archive:
            names = set(archive.getnames())
            for path in git(source, "ls-files", "src", "deploy", "requirements").decode().splitlines():
                self.assertIn(f"{PREFIX}/{path}", names)
            for member in archive:
                target = unpack / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(member)
                    self.assertIsNotNone(stream)
                    with stream:
                        target.write_bytes(stream.read())
                    target.chmod(member.mode)
        extracted = unpack / PREFIX
        self.assertFalse((extracted / ".git").exists())
        inputs = self.directory / "protected-inputs"
        inputs.mkdir(mode=0o700)
        arguments: list[str] = []
        for name, value in installation_inputs().items():
            path = inputs / f"{name}.json"
            path.write_text(json.dumps(value))
            path.chmod(0o600)
            arguments.extend((f"--{name}", str(path)))
        state = self.directory / "installation"
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("IIP_")
        }
        environment["PYTHONNOUSERSITE"] = "1"
        commands = [
            ["community_stack.py", "--state", str(state), "init", *arguments],
            ["community_stack.py", "--state", str(state), "check"],
            ["community_recovery.py", "--help"], ["community_trust.py", "--help"],
        ]
        output = ""
        for script, *arguments in commands:
            result = subprocess.run(
                [sys.executable, str(extracted / "scripts" / script), *arguments],
                cwd=extracted, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
            output += result.stdout + result.stderr
        credentials = json.loads((state / "credentials.json").read_text())
        for credential in credentials.values():
            self.assertNotIn(credential, output)
        self.assertEqual(stat.S_IMODE((state / "credentials.json").stat().st_mode), 0o600)
        self.assertTrue((state / "installation.json").is_file())


if __name__ == "__main__":
    unittest.main()
