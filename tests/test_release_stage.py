from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_stage as stage
from tests import test_release_bundle as fixtures


class ReleaseStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.ReleaseBundleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.finalize()
        self.bundle = self.fixture.bundle
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = f"iip-{fixtures.VERSION}-{fixtures.REVISION[:12]}"
        self.archive = self.root / (self.prefix + ".tar.gz")
        self.restore = self.root / "restored"
        self.restore.mkdir()
        self.source_identity = stage._source_identity
        source = patch.object(stage, "_source_identity", return_value=fixtures.VERSION)
        self.source_mock = source.start()
        self.addCleanup(source.stop)

    def pack(self) -> dict:
        return stage.pack_bundle(self.bundle, self.archive, fixtures.REVISION)

    def unpack(self, receipt: dict, **overrides: str) -> dict:
        return stage.unpack_bundle(self.archive, overrides.get("archive_hash", receipt["fileSha256"]),
                                   overrides.get("manifest_hash", receipt["manifestSha256"]),
                                   fixtures.REVISION, self.restore)

    def rewrite(self, transform) -> str:
        with tarfile.open(self.archive, "r:gz") as source:
            members = [(item, source.extractfile(item).read()) for item in source.getmembers()]
        members = transform(members)
        with self.archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as target:
                for member, content in members:
                    member.size = len(content) if member.isfile() else 0
                    target.addfile(member, io.BytesIO(content) if member.isfile() else None)
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_roundtrip_is_deterministic_exact_and_owner_only(self) -> None:
        receipt = self.pack()
        first = self.archive.read_bytes()
        self.archive.unlink()
        self.assertEqual(self.pack(), receipt)
        self.assertEqual(self.archive.read_bytes(), first)
        self.assertEqual(set(receipt), {"archive", "fileSha256", "manifestSha256", "bundle", "version", "chartVersion"})
        restored = self.unpack(receipt)
        destination = Path(restored["bundle"])
        self.assertEqual(destination, self.restore / self.prefix)
        self.assertEqual(stage.release_bundle.verify_bundle(destination), stage.release_bundle.verify_bundle(self.bundle))
        self.assertEqual({path.name for path in destination.iterdir()}, {path.name for path in self.bundle.iterdir()})
        self.assertFalse((destination / ".stage-incomplete").exists())
        for path in destination.iterdir():
            self.assertEqual(path.read_bytes(), (self.bundle / path.name).read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)

    def test_pack_rejects_extra_credentials_links_and_special_files(self) -> None:
        for mutation in ("credential", "directory", "symlink", "hardlink", "fifo"):
            with self.subTest(mutation=mutation):
                target = self.bundle / "extra-credential"
                if mutation == "credential":
                    target.write_text("fixture-secret-do-not-package")
                elif mutation == "directory":
                    target.mkdir()
                elif mutation == "symlink":
                    target.symlink_to(self.bundle / stage.MANIFEST)
                elif mutation == "hardlink":
                    os.link(self.bundle / stage.MANIFEST, self.root / "hardlinked-manifest")
                else:
                    os.mkfifo(target)
                with self.assertRaises(stage.StageError):
                    self.pack()
                self.assertFalse(self.archive.exists())
                if mutation == "directory":
                    target.rmdir()
                elif mutation == "hardlink":
                    (self.root / "hardlinked-manifest").unlink()
                else:
                    target.unlink()

    def test_trusted_archive_hash_is_checked_before_parsing(self) -> None:
        receipt = self.pack()
        self.archive.write_bytes(b"not-gzip-or-tar")
        with patch.object(stage, "_unpack_stream") as unpack, self.assertRaisesRegex(stage.StageError, "trusted-digest-mismatch"):
            self.unpack(receipt)
        unpack.assert_not_called()
        self.assertFalse(list(self.restore.iterdir()))

    def test_recomputed_outer_hash_cannot_replace_trusted_manifest(self) -> None:
        receipt = self.pack()

        def replace_manifest(members):
            for member, content in members:
                if member.name.endswith("/" + stage.MANIFEST):
                    document = json.loads(content)
                    document["metadata"]["sourceDate"] = "2026-09-01T00:00:00Z"
                    content = json.dumps(document).encode()
                yield member, content

        new_hash = self.rewrite(replace_manifest)
        with self.assertRaisesRegex(stage.StageError, "manifest.trusted-digest-mismatch"):
            self.unpack(receipt, archive_hash=new_hash)
        self.assertFalse(list(self.restore.iterdir()))

    def test_archive_rejects_unsafe_missing_duplicate_and_extra_members(self) -> None:
        receipt = self.pack()
        original = self.archive.read_bytes()
        for mutation in ("traversal", "absolute", "wrong-prefix", "symlink", "hardlink", "directory", "fifo", "duplicate", "extra", "missing"):
            with self.subTest(mutation=mutation):
                self.archive.write_bytes(original)

                def mutate(members):
                    if mutation == "missing":
                        return members[:-1]
                    if mutation == "duplicate":
                        return members + [members[-1]]
                    member = tarfile.TarInfo(self.prefix + "/credential.json")
                    content = b"untrusted"
                    if mutation == "traversal":
                        member.name = self.prefix + "/../escape"
                    elif mutation == "absolute":
                        member.name = "/escape"
                    elif mutation == "wrong-prefix":
                        member.name = "different/credential.json"
                    elif mutation == "symlink":
                        member.type, member.linkname = tarfile.SYMTYPE, "/outside"
                    elif mutation == "hardlink":
                        member.type, member.linkname = tarfile.LNKTYPE, members[0][0].name
                    elif mutation == "directory":
                        member.type = tarfile.DIRTYPE
                    elif mutation == "fifo":
                        member.type = tarfile.FIFOTYPE
                    return members + [(member, content)]

                digest = self.rewrite(mutate)
                with self.assertRaises(stage.StageError):
                    self.unpack(receipt, archive_hash=digest)
                self.assertFalse(list(self.restore.iterdir()))

    def test_artifact_tampering_fails_even_with_trusted_outer_hash_updated(self) -> None:
        receipt = self.pack()

        def change(members):
            for member, content in members:
                if member.name.endswith(".tgz"):
                    content = b"X" + content[1:]
                yield member, content

        digest = self.rewrite(change)
        with self.assertRaisesRegex(stage.StageError, "artifact-digest-mismatch"):
            self.unpack(receipt, archive_hash=digest)

    def test_fully_rechecksummed_substitution_still_requires_original_trusted_manifest(self) -> None:
        receipt = self.pack()

        def substitute(members):
            documents = {member.name.split("/", 1)[1]: content for member, content in members}
            name = f"infra-intelligence-contracts-{fixtures.VERSION}.tar.gz"
            documents[name] = b"substitute-with-fresh-checksums"
            manifest = json.loads(documents[stage.MANIFEST])
            for artifact in manifest["spec"]["artifacts"]:
                if artifact["path"] == name:
                    artifact["sizeBytes"] = len(documents[name])
                    artifact["sha256"] = hashlib.sha256(documents[name]).hexdigest()
            documents[stage.MANIFEST] = json.dumps(manifest).encode()
            documents[stage.CHECKSUMS] = "".join(
                f"{hashlib.sha256(content).hexdigest()}  {filename}\n"
                for filename, content in sorted(documents.items()) if filename != stage.CHECKSUMS
            ).encode()
            return [(member, documents[member.name.split("/", 1)[1]]) for member, _ in members]

        digest = self.rewrite(substitute)
        with self.assertRaisesRegex(stage.StageError, "manifest.trusted-digest-mismatch"):
            self.unpack(receipt, archive_hash=digest)

    def test_corrupt_gzip_with_a_matching_external_hash_has_a_stable_error(self) -> None:
        receipt = self.pack()
        self.archive.write_bytes(b"\x1f\x8b\x08\x00" + bytes(6) + b"\x07broken")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(stage.StageError, "^release-stage.unpack.failed$"):
            self.unpack(receipt, archive_hash=digest)

    def test_exact_source_identity_and_clean_checkout_are_required(self) -> None:
        cases = ((["0" * 40], "revision-mismatch"),
                 ([fixtures.REVISION, " M tracked-file"], "source.dirty"),
                 ([fixtures.REVISION, "?? untracked-file"], "source.dirty"))
        for outputs, error in cases:
            with self.subTest(error=error), patch.object(stage, "_git", side_effect=outputs):
                with self.assertRaisesRegex(stage.StageError, error):
                    self.source_identity(fixtures.REVISION)
        with patch.object(stage, "_git", side_effect=[fixtures.REVISION, "", '[project]\nversion="0.84.0"']):
            self.assertEqual(self.source_identity(fixtures.REVISION), fixtures.VERSION)
        with self.assertRaisesRegex(stage.StageError, "revision.invalid"):
            self.source_identity("main")
        self.source_mock.return_value = "9.9.9"
        with self.assertRaisesRegex(stage.StageError, "manifest.source-mismatch"):
            stage.pack_bundle(self.bundle, self.root / f"iip-9.9.9-{fixtures.REVISION[:12]}.tar.gz", fixtures.REVISION)

    def test_pack_and_unpack_never_overwrite_existing_targets(self) -> None:
        self.archive.write_bytes(b"existing-user-file")
        with self.assertRaisesRegex(stage.StageError, "output.exists"):
            self.pack()
        self.assertEqual(self.archive.read_bytes(), b"existing-user-file")
        self.archive.unlink()
        receipt = self.pack()
        destination = self.restore / self.prefix
        destination.mkdir()
        with self.assertRaisesRegex(stage.StageError, "output.exists"):
            self.unpack(receipt)
        self.assertEqual(list(destination.iterdir()), [])

    def test_racing_directory_is_not_replaced_during_publication(self) -> None:
        receipt = self.pack()
        original = stage._publish_directory

        def collide(staged, destination):
            destination.mkdir()
            (destination / "user-file").write_bytes(b"keep")
            original(staged, destination)

        with patch.object(stage, "_publish_directory", side_effect=collide):
            with self.assertRaisesRegex(stage.StageError, "output.exists"):
                self.unpack(receipt)
        self.assertEqual((self.restore / self.prefix / "user-file").read_bytes(), b"keep")

    def test_bounds_and_exact_archive_names_are_enforced(self) -> None:
        with self.assertRaisesRegex(stage.StageError, "basename-mismatch"):
            stage.pack_bundle(self.bundle, self.root / "other.tar.gz", fixtures.REVISION)
        with patch.object(stage, "MAX_TOTAL_BYTES", 2), self.assertRaises(stage.StageError):
            self.pack()
        receipt = self.pack()
        with patch.object(stage, "MAX_ARCHIVE_BYTES", 2), self.assertRaises(stage.StageError):
            self.unpack(receipt)
        with patch.object(stage, "MAX_FILES", 2), self.assertRaises(stage.StageError):
            self.unpack(receipt)


if __name__ == "__main__":
    unittest.main()
