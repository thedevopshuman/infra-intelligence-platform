from __future__ import annotations

import copy
import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_images as images


APP = "ghcr.io/example/iip@sha256:" + "a" * 64
CONNECTION = {
    "executable": "/usr/local/bin/docker", "endpoint": "unix:///tmp/owned-test-docker.sock",
    "environment": {"PATH": "/usr/local/bin:/usr/bin", "HOME": "/tmp/test-home"},
}


def image_document(reference: str, *, architecture: str = "arm64", image_id: str | None = None) -> dict:
    return {
        "os": "linux", "architecture": architecture,
        "id": image_id or "sha256:" + "b" * 64,
        "repoDigests": [reference],
    }


class CommunityImageReferenceTests(unittest.TestCase):
    def test_strict_registry_digest_parser(self) -> None:
        valid = (
            APP, *images.DEFAULT_IMAGES.values(),
            "registry.example:5000/team/image@sha256:" + "0" * 64,
            "localhost:5000/image@sha256:" + "f" * 64,
            "registry.example/team/with__separator@sha256:" + "f" * 64,
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertEqual(images.require_digest_reference(value), value)
        invalid = (
            None, 1, "", "sha256:" + "a" * 64, "iip:latest", "ghcr.io/team/image:1.0",
            "postgres@sha256:" + "a" * 64, "team/image@sha256:" + "a" * 64,
            "https://" + APP, "user:password@" + APP, APP + "?query=1", APP + "#fragment",
            APP + " ", " " + APP, APP + "\n", APP.replace("/iip@", "/iip:latest@"),
            APP.replace("sha256:", "sha512:"), APP[:-1], APP.replace("a" * 64, "A" * 64),
            APP.replace("ghcr.io", "GHCR.IO"), APP.replace("/iip", "/IIP"),
            APP.replace("/iip", "/../iip"), APP.replace("/iip", "//iip"),
            APP.replace("/iip", "/.iip"), APP.replace("ghcr.io", "registry.example:0"),
            APP.replace("ghcr.io", "registry.example:65536"),
            APP.replace("ghcr.io", "registry.example:05000"),
            APP.replace("ghcr.io", "registry.example:port"),
            APP.replace("ghcr.io", "registry.example:"),
            APP.replace("ghcr.io", "-registry.example"),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(images.CommunityImageError, "reference-invalid"):
                images.require_digest_reference(value)

    def test_defaults_match_packaged_compose_and_return_fresh_mapping(self) -> None:
        content = (ROOT / "deploy/docker-compose.community.yml").read_text()
        declared = dict(re.findall(r"\$\{(IIP_COMMUNITY_[A-Z_]+):-([^}]+)\}", content))
        self.assertEqual(declared, images.DEFAULT_IMAGES)
        selected = images.selected_image_references(APP)
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected["IIP_COMMUNITY_IMAGE"], APP)
        selected["IIP_COMMUNITY_POSTGRES_IMAGE"] = "changed"
        self.assertNotEqual(images.DEFAULT_IMAGES["IIP_COMMUNITY_POSTGRES_IMAGE"], "changed")
        with patch.dict(images.DEFAULT_IMAGES, {"IIP_COMMUNITY_POSTGRES_IMAGE": "postgres:latest"}):
            with self.assertRaises(images.CommunityImageError):
                images.selected_image_references(APP)

    def test_docker_hub_binding_aliases_not_selection_shorthand(self) -> None:
        expected = images.DEFAULT_IMAGES["IIP_COMMUNITY_POSTGRES_IMAGE"]
        suffix = expected.partition("@")[2]
        for alias in (
            f"postgres@{suffix}", f"library/postgres@{suffix}",
            f"docker.io/postgres@{suffix}", f"docker.io/library/postgres@{suffix}",
            f"index.docker.io/library/postgres@{suffix}",
        ):
            document = image_document(alias)
            with self.subTest(alias=alias):
                self.assertEqual(images._resolved_id(document, expected, "arm64"), document["id"])
        expected = images.DEFAULT_IMAGES["IIP_COMMUNITY_COLLECTOR_IMAGE"]
        self.assertEqual(images._resolved_id(image_document(expected.removeprefix("docker.io/")), expected, "arm64"),
                         "sha256:" + "b" * 64)
        with self.assertRaises(images.CommunityImageError):
            images._resolved_id(image_document(APP.removeprefix("ghcr.io/")), APP, "arm64")


class CommunityImageResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.references = images.selected_image_references(APP)
        self.info = {"os": "linux", "architecture": "aarch64"}
        self.documents = {
            reference: image_document(reference, image_id="sha256:" + str(index) * 64)
            for index, reference in enumerate(self.references.values(), start=1)
        }

    def docker_json(self, command: list[str], environment: dict[str, str]) -> object:
        if "info" in command:
            return copy.deepcopy(self.info)
        return copy.deepcopy(self.documents[command[-1]])

    def test_check_only_is_local_and_returns_all_five_exact_ids(self) -> None:
        with patch.object(images, "_docker_json", side_effect=self.docker_json) as observe, patch.object(images.subprocess, "run") as pull:
            result = images.resolve_images(APP, **CONNECTION)
        pull.assert_not_called()
        self.assertEqual(result, {key: self.documents[reference]["id"] for key, reference in self.references.items()})
        self.assertEqual(len(observe.call_args_list), 6)
        for call in observe.call_args_list:
            command, environment = call.args
            self.assertEqual(command[:3], [CONNECTION["executable"], "--host", CONNECTION["endpoint"]])
            self.assertNotIn("pull", command)
            self.assertNotIn("build", command)
            self.assertEqual(environment, CONNECTION["environment"])
        for call, reference in zip(observe.call_args_list[1:], self.references.values(), strict=True):
            self.assertEqual(call.args[0][3:5], ["image", "inspect"])
            self.assertEqual(call.args[0][-1], reference)
            self.assertIn(".RepoDigests", call.args[0][-2])
            self.assertNotIn(".Config", call.args[0][-2])

    def test_pull_is_explicit_platform_pinned_and_logs_suppressed(self) -> None:
        for architecture, normalized in (("aarch64", "arm64"), ("arm64", "arm64"), ("x86_64", "amd64"), ("amd64", "amd64")):
            self.info["architecture"] = architecture
            for document in self.documents.values():
                document["architecture"] = normalized
            with self.subTest(architecture=architecture), patch.object(images, "_docker_json", side_effect=self.docker_json), patch.object(images.subprocess, "run") as pull:
                result = images.resolve_images(APP, pull=True, **CONNECTION)
                self.assertEqual(len(result), 5)
                self.assertEqual(len(pull.call_args_list), 5)
                for call, reference in zip(pull.call_args_list, self.references.values(), strict=True):
                    self.assertEqual(call.args[0], [CONNECTION["executable"], "--host", CONNECTION["endpoint"],
                                                   "pull", "--platform", f"linux/{normalized}", reference])
                    self.assertIs(call.kwargs["stdout"], subprocess.DEVNULL)
                    self.assertIs(call.kwargs["stderr"], subprocess.DEVNULL)
                    self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)
                    self.assertTrue(call.kwargs["check"])
                    self.assertLessEqual(call.kwargs["timeout"], 240)

    def test_unsafe_environment_never_reaches_inspection_or_pull(self) -> None:
        environment = {
            **CONNECTION["environment"], "IIP_AUTH_IDENTITIES_JSON": "secret-identity",
            "IIP_COMMUNITY_IMAGE": "malicious:image", "COMPOSE_FILE": "/tmp/other.yml",
            "COMPOSE_PROFILES": "other", "PGPASSWORD": "database-secret", "PGHOST": "other-host",
            "DOCKER_HOST": "tcp://other:2375", "DOCKER_CONTEXT": "remote",
            "DOCKER_TLS_VERIFY": "0", "DOCKER_CERT_PATH": "/tmp/private",
            "DOCKER_DEFAULT_PLATFORM": "linux/unsupported",
        }
        before = dict(environment)
        with patch.object(images, "_docker_json", side_effect=self.docker_json) as observe, patch.object(images.subprocess, "run") as pull:
            images.resolve_images(APP, **{**CONNECTION, "environment": environment}, pull=True)
        self.assertEqual(environment, before)
        for call in observe.call_args_list:
            self.assertEqual(call.args[1], CONNECTION["environment"])
        for call in pull.call_args_list:
            self.assertEqual(call.kwargs["env"], CONNECTION["environment"])

    def test_invalid_intent_reference_or_connection_fails_before_commands(self) -> None:
        cases = [
            {"pull": value} for value in (1, "true", None)
        ] + [
            {"executable": value} for value in ("docker", "--debug", "", "/usr/bin/../bin/docker", "/bad\0docker")
        ] + [
            {"endpoint": value} for value in ("tcp://host:2375", "unix://host/tmp/docker.sock", "unix:///tmp/../docker.sock",
                                             "unix:///tmp/docker.sock?x=1", "unix:////tmp/docker.sock", "unix:///tmp/has space")
        ] + [
            {"environment": value} for value in (None, {"KEY": None}, {"KEY": "bad\0value"}, {"BAD=KEY": "value"})
        ]
        with patch.object(images, "_docker_json") as observe, patch.object(images.subprocess, "run") as pull:
            for values in cases:
                with self.subTest(values=values), self.assertRaises(images.CommunityImageError):
                    images.resolve_images(APP, **{**CONNECTION, **values})
            with self.assertRaises(images.CommunityImageError):
                images.resolve_images("iip:latest", **CONNECTION)
            observe.assert_not_called()
            pull.assert_not_called()

    def test_unsupported_or_malformed_daemon_prevents_pulls(self) -> None:
        for info in (
            {"os": "windows", "architecture": "amd64"}, {"os": "linux", "architecture": "ppc64le"},
            {"os": "linux", "architecture": None}, {"os": "linux"}, [], None,
            {"os": "linux", "architecture": "arm64", "extra": "untrusted"},
        ):
            with self.subTest(info=info), patch.object(images, "_docker_json", return_value=info), patch.object(images.subprocess, "run") as pull:
                with self.assertRaisesRegex(images.CommunityImageError, "platform-unsupported"):
                    images.resolve_images(APP, **CONNECTION, pull=True)
                pull.assert_not_called()

    def test_missing_wrong_digest_repository_platform_or_id_fails_closed(self) -> None:
        changes = (
            {"repoDigests": None}, {"repoDigests": []}, {"repoDigests": ["ghcr.io/other/image@sha256:" + "a" * 64]},
            {"repoDigests": [APP[:-1] + "b"]}, {"repoDigests": [APP + "\n"]},
            {"repoDigests": [APP] * 129}, {"repoDigests": [None]}, {"os": "windows"},
            {"architecture": "amd64"}, {"architecture": []}, {"id": "tag:latest"},
            {"id": "sha256:" + "B" * 64}, {"extra": "not in inspection contract"},
        )
        original = self.documents[APP]
        for changed in changes:
            self.documents[APP] = {**original, **changed}
            with self.subTest(changed=changed), patch.object(images, "_docker_json", side_effect=self.docker_json), patch.object(images.subprocess, "run") as pull:
                with self.assertRaisesRegex(images.CommunityImageError, "identity-invalid"):
                    images.resolve_images(APP, **CONNECTION)
                pull.assert_not_called()

    def test_inspection_failure_never_implicitly_pulls(self) -> None:
        with patch.object(images, "_docker_json", side_effect=[self.info, images.CommunityImageError("community.images.command-failed")]), patch.object(images.subprocess, "run") as pull:
            with self.assertRaises(images.CommunityImageError):
                images.resolve_images(APP, **CONNECTION)
            pull.assert_not_called()

    def test_failed_or_timed_out_pull_stops_without_output_or_inspection(self) -> None:
        failures = (
            subprocess.CalledProcessError(1, ["docker"], output="private registry text", stderr="secret credential"),
            subprocess.TimeoutExpired(["docker"], 240, output="private registry text", stderr="secret credential"),
            OSError("secret local path"),
        )
        for failure in failures:
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(failure=type(failure).__name__), redirect_stdout(output), redirect_stderr(errors), patch.object(images, "_docker_json", side_effect=self.docker_json) as observe, patch.object(images.subprocess, "run", side_effect=failure) as pull:
                with self.assertRaises(images.CommunityImageError) as caught:
                    images.resolve_images(APP, **CONNECTION, pull=True)
                self.assertEqual(str(caught.exception), "community.images.command-failed")
                self.assertEqual(len(observe.call_args_list), 1)
                self.assertEqual(len(pull.call_args_list), 1)
                self.assertEqual(output.getvalue() + errors.getvalue(), "")

    def test_no_partial_mapping_after_later_dependency_failure(self) -> None:
        reference = list(self.references.values())[3]
        self.documents[reference]["repoDigests"] = []
        with patch.object(images, "_docker_json", side_effect=self.docker_json), patch.object(images.subprocess, "run") as pull:
            with self.assertRaises(images.CommunityImageError):
                images.resolve_images(APP, **CONNECTION, pull=True)
            self.assertEqual(len(pull.call_args_list), 4)


class CommunityImageCommandBoundaryTests(unittest.TestCase):
    def python_json(self, code: str) -> object:
        # Exercise the output boundary with an ordinary local Python process,
        # never Docker, registries, providers or initialized installation state.
        return images._docker_json([sys.executable, "-c", code], {"PATH": os.defpath})

    def test_valid_json_and_stderr_minimization(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(self.python_json('import sys; print("{\\"os\\":\\"linux\\"}"); print("private stderr", file=sys.stderr)'), {"os": "linux"})
        self.assertEqual(output.getvalue() + errors.getvalue(), "")

    def test_real_output_limit_and_timeout(self) -> None:
        with patch.object(images, "_MAX_OUTPUT_BYTES", 128):
            with self.assertRaisesRegex(images.CommunityImageError, "output-invalid"):
                self.python_json('import sys; sys.stdout.write("x" * 1000000); sys.stdout.flush()')
        with patch.object(images, "_INSPECTION_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(images.CommunityImageError, "command-failed"):
                self.python_json("import time; time.sleep(10)")

    def test_nonzero_malformed_duplicate_and_non_utf8_output_are_stable(self) -> None:
        cases = (
            'import sys; print("private failure"); sys.exit(1)',
            'print("not JSON private content")',
            'print("{\\"id\\":1,\\"id\\":2}")',
            'import sys; sys.stdout.buffer.write(bytes([255]))',
        )
        for code in cases:
            with self.subTest(code=code), self.assertRaises(images.CommunityImageError) as caught:
                self.python_json(code)
            self.assertIn(str(caught.exception), {"community.images.output-invalid", "community.images.command-failed"})
            self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
