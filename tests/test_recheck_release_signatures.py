from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import patch

from scripts import recheck_release_signatures as diagnostic
from scripts.release_signature_verification import VerifiedSignature


DIGESTS = ("sha256:" + "a" * 64, "sha256:" + "b" * 64)


class RecheckReleaseSignaturesTests(unittest.TestCase):
    def arguments(self, *, tag: str = "v0.84.2", digests: tuple[str, str] = DIGESTS) -> list[str]:
        return ["--tag", tag, "--control-plane-digest", digests[0], "--bridge-digest", digests[1]]

    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = diagnostic.main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_valid_release_tags_bind_exact_fixed_repositories_issuer_and_identity(self) -> None:
        self.assertEqual(diagnostic.REPOSITORIES, (
            ("control-plane-image", "docker.io/thedevopshuman/iip"),
            ("plugin-mediation-bridge-image", "docker.io/thedevopshuman/iip-bridge"),
        ))
        for tag in ("v0.84.2", "v1.0.0-rc.1"):
            with self.subTest(tag=tag), patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
                runner = constructor.return_value
                runner.version.return_value = diagnostic.release_publication.COSIGN_VERSION
                runner.verify.return_value = VerifiedSignature("github-release-workflow", 1)
                status, output, errors = self.invoke(self.arguments(tag=tag))
                self.assertEqual(status, 0)
                self.assertEqual(errors, "")
                constructor.assert_called_once_with("scripts/cosign_container.sh")
                runner.version.assert_called_once_with()
                self.assertEqual(runner.verify.call_count, 2)
                for call, (role, repository), digest in zip(
                    runner.verify.call_args_list, diagnostic.REPOSITORIES, DIGESTS,
                ):
                    self.assertEqual(call.kwargs, {
                        "reference": repository + "@" + digest,
                        "repository": repository,
                        "digest": digest,
                        "trust": {"mode": "keyless", "identities": [{
                            "id": "github-release-workflow",
                            "certificateIdentity": (
                                "https://github.com/thedevopshuman/infra-intelligence-platform/"
                                ".github/workflows/release.yml@refs/tags/" + tag
                            ),
                            "certificateOidcIssuer": "https://token.actions.githubusercontent.com",
                        }]},
                        "transparency_mode": "required",
                    })
                    self.assertIn(f"{role} {digest} verified-count=1\n", output)
                self.assertTrue(output.endswith(diagnostic.LIMITATION + "\n"))

    def test_invalid_tag_or_digest_is_rejected_before_runner_construction(self) -> None:
        invalid = [self.arguments(tag=tag) for tag in (
            "", "0.84.2", "V0.84.2", "v0.84", "v0.84.2+build", "v0.84.2/private",
            "refs/tags/v0.84.2", "v0.84.2\n", "v0.84.2#fragment", "v0.84.2-" + "a" * 130,
        )]
        for value in ("", "a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63,
                      "sha256:" + "a" * 65, "sha256:" + "a" * 64 + "\n", "repository@" + DIGESTS[0]):
            invalid.extend((self.arguments(digests=(value, DIGESTS[1])),
                            self.arguments(digests=(DIGESTS[0], value))))
        invalid.extend(([], [*self.arguments(), "--unknown", "private-input-must-not-appear"]))
        for arguments in invalid:
            with self.subTest(arguments=arguments), patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
                status, output, errors = self.invoke(arguments)
                self.assertEqual(status, 2)
                constructor.assert_not_called()
                self.assertEqual(errors, "release-signature.diagnostic.arguments-invalid\n")
                self.assertEqual(output, diagnostic.LIMITATION + "\n")

    def test_wrong_version_prevents_all_signature_calls(self) -> None:
        with patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
            runner = constructor.return_value
            runner.version.return_value = "3.1.3"
            status, output, errors = self.invoke(self.arguments())
            self.assertEqual(status, 2)
            self.assertEqual(errors, "release-signature.tool.version-mismatch\n")
            self.assertEqual(output, diagnostic.LIMITATION + "\n")
            runner.verify.assert_not_called()

    def test_either_role_failure_rejects_diagnostic_and_still_checks_the_other(self) -> None:
        for failed in (0, 1):
            with self.subTest(failed=failed), patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
                runner = constructor.return_value
                runner.version.return_value = diagnostic.release_publication.COSIGN_VERSION
                outcomes = [VerifiedSignature("github-release-workflow", 1)] * 2
                outcomes[failed] = diagnostic.ReleaseSignatureError("release-signature.signature.rejected")
                runner.verify.side_effect = outcomes
                status, output, errors = self.invoke(self.arguments())
                self.assertEqual(status, 2)
                self.assertEqual(runner.verify.call_count, 2)
                self.assertEqual(errors, f"{diagnostic.REPOSITORIES[failed][0]} {DIGESTS[failed]} release-signature.signature.rejected\n")
                self.assertTrue(output.endswith(diagnostic.LIMITATION + "\n"))
                self.assertNotIn(f"{diagnostic.REPOSITORIES[failed][0]} {DIGESTS[failed]} verified", output)

    def test_tool_failure_is_stable_and_never_reflects_unknown_error_text(self) -> None:
        for message, expected in (
            ("release-signature.tool.unavailable", "release-signature.tool.unavailable"),
            ("private-provider-text", "release-signature.diagnostic.failed"),
        ):
            with self.subTest(message=message), patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
                runner = constructor.return_value
                runner.version.side_effect = diagnostic.ReleaseSignatureError(message)
                status, output, errors = self.invoke(self.arguments())
                self.assertEqual(status, 2)
                self.assertEqual(errors, expected + "\n")
                self.assertEqual(output, diagnostic.LIMITATION + "\n")
                runner.verify.assert_not_called()

    def test_explicit_cosign_path_is_passed_without_shell_or_extra_command(self) -> None:
        with patch.object(diagnostic, "SubprocessCosignRunner") as constructor:
            runner = constructor.return_value
            runner.version.return_value = diagnostic.release_publication.COSIGN_VERSION
            runner.verify.return_value = VerifiedSignature("github-release-workflow", 1)
            self.assertEqual(self.invoke([*self.arguments(), "--cosign", "/opt/cosign"])[0], 0)
            constructor.assert_called_once_with("/opt/cosign")


if __name__ == "__main__":
    unittest.main()
