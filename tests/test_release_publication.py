from __future__ import annotations

import copy
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release_publication as publication
from scripts import release_signature_verification as signatures
from scripts.release_bundle import finalize_bundle
from tests.test_release_bundle import write_oci_fixture


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.84.0"
CHART_VERSION = "0.87.0"
SDK_VERSION = "0.64.0"


class RecordingPublisher:
    def __init__(self, *, fail_role: str | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_role = fail_role

    def version(self) -> str:
        return "0.36.0"

    def publish(self, **request: object) -> None:
        self.calls.append(dict(request))
        if self.fail_role and self.fail_role in str(request["repository"]):
            raise publication.ReleasePublicationError(
                "release-publication.copy.failed"
            )


def current_revision() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def repositories(host: str = "ghcr.io") -> dict[str, str]:
    return {
        publication.ROLES[0]: f"{host}/acme/infra-intelligence-platform",
        publication.ROLES[1]: (
            f"{host}/acme/infra-intelligence-platform-plugin-mediation-bridge"
        ),
    }


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        for role in ("control-plane", "plugin-mediation-bridge"):
            write_oci_fixture(
                self.bundle / f"infra-intelligence-{role}-{VERSION}.oci.tar",
                marker=role,
            )
        for filename in (
            f"infra-intelligence-{CHART_VERSION}.tgz",
            f"infra-intelligence-contracts-{VERSION}.tar.gz",
            f"infra-intelligence-sdk-{SDK_VERSION}.tar.gz",
            f"iip-sdk-{SDK_VERSION}.tgz",
        ):
            (self.bundle / filename).write_bytes(filename.encode("ascii"))
        finalize_bundle(
            self.bundle,
            version=VERSION,
            chart_version=CHART_VERSION,
            python_sdk_version=SDK_VERSION,
            typescript_sdk_version=SDK_VERSION,
            revision=current_revision(),
            source_date="2026-09-07T08:00:00Z",
            platforms=("linux/amd64", "linux/arm64"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish(
        self,
        publisher: RecordingPublisher | None = None,
    ) -> tuple[dict[str, object], RecordingPublisher]:
        selected = publisher or RecordingPublisher()
        report = publication.publish_candidate(
            bundle=self.bundle,
            repositories=repositories(),
            tag=f"v{VERSION}",
            output=self.root / "publication.json",
            publisher=selected,
        )
        return report, selected

    def test_exact_bundle_indexes_are_published_and_reported(self) -> None:
        report, publisher = self.publish()

        self.assertEqual(len(publisher.calls), 2)
        manifest = json.loads(
            (self.bundle / "release-manifest.json").read_text(encoding="utf-8")
        )
        expected = (
            manifest["spec"]["image"]["indexDigest"],
            manifest["spec"]["pluginMediationBridgeImage"]["indexDigest"],
        )
        for role, call, digest in zip(publication.ROLES, publisher.calls, expected):
            self.assertEqual(call["repository"], repositories()[role])
            self.assertEqual(call["tag"], f"v{VERSION}")
            self.assertEqual(call["digest"], digest)
            self.assertTrue(Path(call["archive"]).is_file())

        publication.validate_report(report)
        self.assertEqual(report["metadata"]["id"], publication.report_id(report))
        self.assertEqual(
            [target["indexDigest"] for target in report["spec"]["targets"]],
            list(expected),
        )
        self.assertEqual(
            report["spec"]["promotionStatus"],
            "requires-signature-and-vulnerability-qualification",
        )

    def test_wrong_tag_repository_or_partial_copy_never_writes_success(self) -> None:
        output = self.root / "publication.json"
        with self.assertRaisesRegex(
            publication.ReleasePublicationError,
            "release-publication.tag.invalid",
        ):
            publication.publish_candidate(
                bundle=self.bundle,
                repositories=repositories(),
                tag="latest",
                output=output,
                publisher=RecordingPublisher(),
            )
        self.assertFalse(output.exists())

        output.write_text("retained evidence\n", encoding="utf-8")
        untouched = RecordingPublisher()
        with self.assertRaisesRegex(
            publication.ReleasePublicationError,
            "release-publication.output.exists",
        ):
            publication.publish_candidate(
                bundle=self.bundle,
                repositories=repositories(),
                tag=f"v{VERSION}",
                output=output,
                publisher=untouched,
            )
        self.assertEqual(output.read_text(encoding="utf-8"), "retained evidence\n")
        self.assertEqual(untouched.calls, [])
        output.unlink()

        crossed = repositories()
        crossed[publication.ROLES[1]] = crossed[publication.ROLES[0]]
        with self.assertRaisesRegex(
            publication.ReleasePublicationError,
            "release-publication.repositories.invalid",
        ):
            publication.publish_candidate(
                bundle=self.bundle,
                repositories=crossed,
                tag=f"v{VERSION}",
                output=output,
                publisher=RecordingPublisher(),
            )
        self.assertFalse(output.exists())

        with self.assertRaisesRegex(
            publication.ReleasePublicationError,
            "release-publication.copy.failed",
        ):
            publication.publish_candidate(
                bundle=self.bundle,
                repositories=repositories(),
                tag=f"v{VERSION}",
                output=output,
                publisher=RecordingPublisher(fail_role="plugin-mediation-bridge"),
            )
        self.assertFalse(output.exists())

    def test_report_relationships_and_content_identity_fail_closed(self) -> None:
        report, _ = self.publish()
        mutations = (
            lambda value: value["spec"]["release"].update({"tag": "v9.9.9"}),
            lambda value: value["spec"]["targets"][0].update(
                {"immutableReference": "ghcr.io/acme/substituted@sha256:" + "f" * 64}
            ),
            lambda value: value["spec"]["targets"].reverse(),
            lambda value: value["metadata"].update({"id": "rpr_" + "0" * 32}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(
                publication.ReleasePublicationError,
                "release-publication.report.invalid",
            ):
                publication.validate_report(changed)

    def test_github_policy_binds_exact_workflow_tag_and_repositories(self) -> None:
        report, _ = self.publish()

        policy = publication.github_signature_policy(
            report,
            github_repository="acme/infra-intelligence-platform",
            generation=17,
            effective_at="2026-09-07T08:00:00Z",
        )

        signatures.validate_policy_document(policy, promotion=True)
        self.assertEqual(policy["metadata"]["generation"], 17)
        artifacts = policy["spec"]["artifacts"]
        self.assertEqual(
            [artifact["repository"] for artifact in artifacts],
            list(repositories().values()),
        )
        for artifact in artifacts:
            identity = artifact["trust"]["identities"][0]
            self.assertEqual(
                identity["certificateIdentity"],
                "https://github.com/acme/infra-intelligence-platform/"
                ".github/workflows/release.yml@refs/tags/v0.84.0",
            )
            self.assertEqual(
                identity["certificateOidcIssuer"],
                "https://token.actions.githubusercontent.com",
            )

    def test_github_release_context_requires_exact_clean_main_tip(self) -> None:
        revision = current_revision()
        version = publication.project_version()
        with patch.object(publication, "source_identity", return_value=(revision, False)):
            publication.validate_github_release_context(
                tag=f"v{version}",
                revision=revision,
                main_revision=revision,
                github_repository="acme/infra-intelligence-platform",
            )

            rejected = (
                ({"tag": "v9.9.9"}, "release-publication.tag.invalid"),
                (
                    {"main_revision": "f" * 40},
                    "release-publication.source.mismatch",
                ),
                (
                    {"github_repository": "invalid"},
                    "release-publication.github.invalid",
                ),
            )
            defaults = {
                "tag": f"v{version}",
                "revision": revision,
                "main_revision": revision,
                "github_repository": "acme/infra-intelligence-platform",
            }
            for changed, code in rejected:
                arguments = defaults | changed
                with self.subTest(arguments=arguments), self.assertRaisesRegex(
                    publication.ReleasePublicationError,
                    code,
                ):
                    publication.validate_github_release_context(**arguments)

        with patch.object(publication, "source_identity", return_value=(revision, True)):
            with self.assertRaisesRegex(
                publication.ReleasePublicationError,
                "release-publication.source.dirty",
            ):
                publication.validate_github_release_context(
                    tag=f"v{version}",
                    revision=revision,
                    main_revision=revision,
                    github_repository="acme/infra-intelligence-platform",
                )

    def test_oci_extraction_rejects_traversal_and_links(self) -> None:
        for name, kind in (("../escape", "file"), ("link", "link")):
            archive_path = self.root / f"{kind}.tar"
            with tarfile.open(archive_path, mode="w") as archive:
                member = tarfile.TarInfo(name)
                if kind == "link":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/etc/passwd"
                else:
                    member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
            destination = self.root / f"extract-{kind}"
            destination.mkdir()
            with self.subTest(kind=kind), self.assertRaisesRegex(
                publication.ReleasePublicationError,
                "release-publication.archive.invalid",
            ):
                publication._extract_oci_layout(archive_path, destination)
        self.assertFalse((self.root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
