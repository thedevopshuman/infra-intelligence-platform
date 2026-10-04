from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import recover_oci_layout as recovery
from scripts.release_bundle import REQUIRED_PREDICATES


def canonical(document: object) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def fixture(root: Path, *, platforms: tuple[str, ...] = ("amd64", "arm64"),
            include_sbom: bool = True, layer_size_error: bool = False,
            media: str | None = None, external: bool = False,
            inline_data: str | None = None) -> tuple[str, dict[str, bytes]]:
    blobs: dict[str, bytes] = {}

    def store(content: bytes, media_type: str) -> dict:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        blobs[digest] = content
        return {"mediaType": media_type, "digest": digest, "size": len(content)}

    def document(value: object, media_type: str) -> dict:
        return store(canonical(value), media_type)

    manifests = []
    for architecture in platforms:
        layer = store(("layer-" + architecture).encode(), media or "application/vnd.oci.image.layer.v1.tar")
        if layer_size_error:
            layer["size"] += 1
        if external:
            layer["urls"] = ["https://untrusted.invalid/blob"]
        config = document({"architecture": architecture, "os": "linux", "config": {},
                           "rootfs": {"type": "layers", "diff_ids": [layer["digest"]]}}, recovery.CONFIG)
        image = document({"schemaVersion": 2, "mediaType": recovery.OCI_MANIFEST,
                          "config": config, "layers": [layer]}, recovery.OCI_MANIFEST)
        manifests.append(dict(image, platform={"os": "linux", "architecture": architecture}))
        attestations = []
        for predicate in sorted(REQUIRED_PREDICATES):
            if not include_sbom and predicate.endswith("Document"):
                continue
            statement = document({"_type": "https://in-toto.io/Statement/v1", "predicateType": predicate,
                                  "subject": [{"name": "fixture", "digest": {"sha256": image["digest"].split(":")[1]}}],
                                  "predicate": {"fixtureArchitecture": architecture}}, recovery.IN_TOTO)
            attestations.append(statement)
        empty_config = document({}, recovery.EMPTY)
        if inline_data is not None:
            empty_config["data"] = inline_data
        attestation = document({"schemaVersion": 2, "mediaType": recovery.OCI_MANIFEST,
                                "config": empty_config, "layers": attestations,
                                "subject": image}, recovery.OCI_MANIFEST)
        manifests.append(dict(attestation, platform={"os": "unknown", "architecture": "unknown"}, annotations={
            "vnd.docker.reference.type": "attestation-manifest", "vnd.docker.reference.digest": image["digest"],
        }))
    index = document({"schemaVersion": 2, "mediaType": recovery.OCI_INDEX, "manifests": manifests}, recovery.OCI_INDEX)
    blob_directory = root / "blobs" / "sha256"
    blob_directory.mkdir(parents=True)
    for digest, content in blobs.items():
        (blob_directory / digest.split(":")[1]).write_bytes(content)
    (root / "oci-layout").write_bytes(canonical({"imageLayoutVersion": "1.0.0"}))
    (root / "index.json").write_bytes(canonical({"schemaVersion": 2, "manifests": [index]}))
    return index["digest"], blobs


class OciLayoutRecoveryTests(unittest.TestCase):
    def test_buildx_inline_empty_config_is_verified_without_replacing_local_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, _ = fixture(root / "layout", inline_data="e30=")
            output = root / "archive.tar"
            result = recovery.recover_layout(root / "layout", digest, output)
            self.assertEqual(recovery.verify_archive(output, digest), result)
            empty_digest = hashlib.sha256(b"{}").hexdigest()
            (root / "layout/blobs/sha256" / empty_digest).unlink()
            with self.assertRaisesRegex(recovery.RecoveryError, "graph.blob-missing"):
                recovery.recover_layout(root / "layout", digest, root / "missing.tar")

    def test_inline_content_requires_canonical_base64_matching_size_and_digest(self) -> None:
        for encoded in ("!invalid", "eA==", "e10=", "e31=", "e30=\n", "\N{SNOWMAN}"):
            with self.subTest(encoded=encoded), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, _ = fixture(root / "layout", inline_data=encoded)
                with self.assertRaisesRegex(recovery.RecoveryError, "descriptor.inline-data"):
                    recovery.recover_layout(root / "layout", digest, root / "archive.tar")

    def test_complete_graph_preserves_raw_index_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, blobs = fixture(root / "layout")
            first, second = root / "first.tar", root / "second.tar"
            result = recovery.recover_layout(root / "layout", digest, first)
            self.assertEqual(recovery.verify_archive(first, digest), result)
            recovery.recover_layout(root / "layout", digest, second)
            self.assertEqual(result["indexDigest"], digest)
            self.assertEqual({item["name"] for item in result["platforms"]}, recovery.PLATFORMS)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                names = archive.getnames()
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(archive.extractfile("blobs/sha256/" + digest.split(":")[1]).read(), blobs[digest])
                for member in archive.getmembers():
                    self.assertTrue(member.isfile())
                    self.assertEqual((member.uid, member.gid, member.mtime, member.mode), (0, 0, 0, 0o644))
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_tampered_or_missing_blobs_leave_no_archive(self) -> None:
        for mutation in ("missing-layer", "tampered-layer", "tampered-config", "tampered-attestation", "tampered-index"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, blobs = fixture(root / "layout")
                if mutation.endswith("layer"):
                    target = next(key for key, value in blobs.items() if value.startswith(b"layer-"))
                elif mutation.endswith("config"):
                    target = next(key for key, value in blobs.items() if b'"rootfs"' in value)
                elif mutation.endswith("attestation"):
                    target = next(key for key, value in blobs.items() if b'"predicateType"' in value)
                else:
                    target = digest
                path = root / "layout/blobs/sha256" / target.split(":")[1]
                if mutation.startswith("missing"):
                    path.unlink()
                else:
                    content = path.read_bytes()
                    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
                with self.assertRaises(recovery.RecoveryError):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")
                self.assertFalse((root / "output.tar").exists())
                self.assertFalse(list(root.glob(".iip-oci-recovery-*")))

    def test_graph_descriptor_media_size_urls_platform_and_attestation_rejections(self) -> None:
        cases = ({"layer_size_error": True}, {"media": "application/unsupported"}, {"external": True},
                 {"platforms": ("amd64",)}, {"platforms": ("amd64", "arm64", "386")}, {"include_sbom": False})
        for arguments in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, _ = fixture(root / "layout", **arguments)
                with self.assertRaises(recovery.RecoveryError):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")
                self.assertFalse((root / "output.tar").exists())

    def test_expected_digest_and_layout_wrapper_are_exact(self) -> None:
        for mutation in ("expected", "duplicate-root", "duplicate-json-key", "path-digest", "root-subject"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, _ = fixture(root / "layout")
                index_path = root / "layout/index.json"
                document = json.loads(index_path.read_bytes())
                if mutation == "expected":
                    digest = "sha256:" + "0" * 64
                elif mutation == "duplicate-root":
                    document["manifests"].append(document["manifests"][0])
                    index_path.write_bytes(canonical(document))
                elif mutation == "duplicate-json-key":
                    index_path.write_bytes(b'{"schemaVersion":2,' + index_path.read_bytes()[1:])
                elif mutation == "path-digest":
                    document["manifests"][0]["digest"] = "../../outside"
                    index_path.write_bytes(canonical(document))
                else:
                    document["subject"] = {"urls": ["https://untrusted.invalid"]}
                    index_path.write_bytes(canonical(document))
                with self.assertRaises(recovery.RecoveryError):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")

    def test_layout_rejects_unreferenced_blobs_unknown_members_symlinks_hardlinks_and_special_files(self) -> None:
        for mutation in ("extra-blob", "extra-top-level", "blob-symlink", "directory-symlink", "hardlink", "fifo"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, blobs = fixture(root / "layout")
                blob_path = root / "layout/blobs/sha256" / next(iter(blobs)).split(":")[1]
                if mutation == "extra-blob":
                    extra = b"unreferenced"
                    (blob_path.parent / hashlib.sha256(extra).hexdigest()).write_bytes(extra)
                elif mutation == "extra-top-level":
                    (root / "layout/unexpected").write_text("ignored?")
                elif mutation == "blob-symlink":
                    outside = root / "outside"
                    blob_path.rename(outside)
                    blob_path.symlink_to(outside)
                elif mutation == "directory-symlink":
                    (root / "layout/blobs/sha256").rename(root / "outside")
                    (root / "layout/blobs/sha256").symlink_to(root / "outside", target_is_directory=True)
                elif mutation == "hardlink":
                    os.link(blob_path, root / "outside")
                else:
                    blob_path.unlink()
                    os.mkfifo(blob_path)
                with self.assertRaises(recovery.RecoveryError):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")
                self.assertFalse((root / "output.tar").exists())

    def test_bounded_layout_and_metadata(self) -> None:
        for constant, bound in (("MAX_FILES", 2), ("MAX_BLOB_BYTES", 2), ("MAX_TOTAL_BYTES", 2), ("MAX_METADATA_BYTES", 2)):
            with self.subTest(constant=constant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, _ = fixture(root / "layout")
                with patch.object(recovery, constant, bound), self.assertRaises(recovery.RecoveryError):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")

    def test_output_never_overwrites_existing_or_racing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, _ = fixture(root / "layout")
            output = root / "output.tar"
            output.write_bytes(b"owned-by-user")
            with self.assertRaisesRegex(recovery.RecoveryError, "output.exists"):
                recovery.recover_layout(root / "layout", digest, output)
            self.assertEqual(output.read_bytes(), b"owned-by-user")
            output.unlink()
            original = recovery.os.link

            def racing_file(source: Path, target: Path, **kwargs: object) -> None:
                target.write_bytes(b"racing-file")
                original(source, target, **kwargs)

            with patch.object(recovery.os, "link", side_effect=racing_file):
                with self.assertRaisesRegex(recovery.RecoveryError, "output.exists"):
                    recovery.recover_layout(root / "layout", digest, output)
            self.assertEqual(output.read_bytes(), b"racing-file")
            self.assertFalse(list(root.glob(".iip-oci-recovery-*")))

    def test_layout_mutation_before_packaging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, blobs = fixture(root / "layout")
            target = next(key for key, value in blobs.items() if value.startswith(b"layer-"))
            original = recovery._validate_graph

            def tamper(*args: object) -> dict:
                result = original(*args)
                path = root / "layout/blobs/sha256" / target.split(":")[1]
                content = path.read_bytes()
                path.write_bytes(b"X" + content[1:])
                return result

            with patch.object(recovery, "_validate_graph", side_effect=tamper):
                with self.assertRaisesRegex(recovery.RecoveryError, "changed-during-packaging"):
                    recovery.recover_layout(root / "layout", digest, root / "output.tar")
            self.assertFalse((root / "output.tar").exists())

    def test_archive_checkpoint_rejects_tampered_layer_despite_recomputed_outer_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, blobs = fixture(root / "layout")
            archive = root / "output.tar"
            recovery.recover_layout(root / "layout", digest, archive)
            original_checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            layer = next(key for key, content in blobs.items() if content.startswith(b"layer-"))
            with tarfile.open(archive) as packed:
                offset = packed.getmember("blobs/sha256/" + layer.split(":")[1]).offset_data
            with archive.open("r+b") as target:
                target.seek(offset)
                target.write(b"X")
            recomputed_checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertNotEqual(original_checksum, recomputed_checksum)
            # A replacement SHA256SUMS could agree with this new outer digest;
            # the immutable index's transitive layer binding must still reject it.
            with self.assertRaisesRegex(recovery.RecoveryError, "graph.digest-mismatch"):
                recovery.verify_archive(archive, digest)

    def test_archive_checkpoint_refuses_duplicate_unknown_link_and_missing_members(self) -> None:
        for mutation in ("duplicate", "unknown", "symlink", "missing-layer", "extra-blob", "size", "trailing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                digest, blobs = fixture(root / "layout")
                source, altered = root / "source.tar", root / "altered.tar"
                recovery.recover_layout(root / "layout", digest, source)
                layer = "blobs/sha256/" + next(key for key, content in blobs.items() if content.startswith(b"layer-")).split(":")[1]
                with tarfile.open(source) as original, tarfile.open(altered, "w", format=tarfile.USTAR_FORMAT) as target:
                    for member in original.getmembers():
                        if mutation == "missing-layer" and member.name == layer:
                            continue
                        content = original.extractfile(member).read()
                        if mutation == "size" and member.name == layer:
                            content += b"X"
                            member.size = len(content)
                        target.addfile(member, io.BytesIO(content))
                        if mutation == "duplicate" and member.name == "index.json":
                            target.addfile(member, io.BytesIO(content))
                    if mutation in ("unknown", "extra-blob", "symlink"):
                        name = "../unexpected" if mutation == "unknown" else "blobs/sha256/" + "0" * 64
                        member = tarfile.TarInfo(name)
                        if mutation == "symlink":
                            member.type = tarfile.SYMTYPE
                            member.linkname = "/outside"
                            target.addfile(member)
                        else:
                            member.size = 1
                            target.addfile(member, io.BytesIO(b"X"))
                if mutation == "trailing":
                    with altered.open("ab") as target:
                        target.write(b"hidden content")
                with self.assertRaises(recovery.RecoveryError):
                    recovery.verify_archive(altered, digest)

    def test_archive_checkpoint_expected_digest_and_file_type_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest, _ = fixture(root / "layout")
            archive = root / "archive.tar"
            recovery.recover_layout(root / "layout", digest, archive)
            with self.assertRaisesRegex(recovery.RecoveryError, "expected-index-mismatch"):
                recovery.verify_archive(archive, "sha256:" + "0" * 64)
            linked = root / "linked.tar"
            linked.symlink_to(archive)
            with self.assertRaises(recovery.RecoveryError):
                recovery.verify_archive(linked, digest)


if __name__ == "__main__":
    unittest.main()
