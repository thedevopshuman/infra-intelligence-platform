from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import ast
import copy
import io
import json
from pathlib import Path
import stat
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts import cosign_recovery_adapter as adapter


# Frozen validation boundary from cd95235d0c95ffa011eeea4fec3edf89faac9a2a,
# scripts/release_signature_verification.py:_validated_cosign_output. Keep the
# original literal hostname comparison: this test must not use the fixed parser.
def original_parser(encoded: bytes, *, repository: str, reference: str, digest: str) -> int:
    if not encoded or len(encoded) > 2_097_152:
        raise ValueError("invalid")
    document = json.loads(encoded)
    if not isinstance(document, list) or not 1 <= len(document) <= 16:
        raise ValueError("invalid")
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("invalid")
        critical = item.get("critical")
        identity = critical.get("identity") if isinstance(critical, dict) else None
        image = critical.get("image") if isinstance(critical, dict) else None
        if (not isinstance(identity, dict)
                or identity.get("docker-reference") not in (repository, reference)
                or not isinstance(image, dict)
                or image.get("docker-manifest-digest") != digest
                or critical.get("type") != "https://sigstore.dev/cosign/sign/v1"):
            raise ValueError("invalid")
    return len(document)


class CosignRecoveryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository, self.digest = next(iter(adapter.TARGETS.items()))
        environment = patch.dict(adapter.os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        adapter.os.environ.pop("IIP_RECOVERY_DOCKER_HOST", None)

    def arguments(self, repository: str | None = None, digest: str | None = None) -> tuple[str, ...]:
        return (*adapter.VERIFY_PREFIX, (repository or self.repository) + "@" + (digest or self.digest))

    def document(self, *, repository: str | None = None, digest: str | None = None) -> list[dict]:
        repository, digest = repository or self.repository, digest or self.digest
        return [{"critical": {
            "identity": {"docker-reference": repository.replace("docker.io/", "index.docker.io/", 1) + "@" + digest},
            "image": {"docker-manifest-digest": digest},
            "type": adapter.SIGNATURE_TYPE,
        }, "optional": {"unchanged": [1, "synthetic", None], "issuer": adapter.ISSUER}}]

    def invoke(self, arguments, document=None, *, status=0, raw: bytes | None = None):
        stdout, stderr = io.StringIO(), io.StringIO()
        result = subprocess.CompletedProcess([], status,
                                             raw if raw is not None else json.dumps(document).encode(),
                                             b"private provider diagnostic")
        with patch.object(adapter.subprocess, "run", return_value=result) as run:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = adapter.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue(), run

    def test_exact_targets_normalize_only_alias_after_success(self) -> None:
        for repository, digest in adapter.TARGETS.items():
            for include_digest in (True, False):
                document = self.document(repository=repository, digest=digest)
                if not include_digest:
                    document[0]["critical"]["identity"]["docker-reference"] = repository.replace("docker.io/", "index.docker.io/", 1)
                with self.assertRaises(ValueError):
                    original_parser(json.dumps(document).encode(), repository=repository,
                                    reference=repository + "@" + digest, digest=digest)
                code, output, errors, run = self.invoke(self.arguments(repository, digest), document)
                self.assertEqual((code, errors), (0, ""))
                expected = copy.deepcopy(document)
                expected[0]["critical"]["identity"]["docker-reference"] = repository + ("@" + digest if include_digest else "")
                self.assertEqual(json.loads(output), expected)
                self.assertEqual(original_parser(output.encode(), repository=repository,
                                                reference=repository + "@" + digest, digest=digest), 1)
                self.assertEqual(run.call_args.args[0], (str(adapter.WRAPPER), *self.arguments(repository, digest)))
                self.assertEqual(run.call_count, 1)

    def test_canonical_claim_remains_unchanged(self) -> None:
        document = self.document()
        document[0]["critical"]["identity"]["docker-reference"] = self.repository + "@" + self.digest
        code, output, errors, _ = self.invoke(self.arguments(), document)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output), document)

    def test_actual_original_git_parser_accepts_only_correct_normalized_claims(self) -> None:
        result = subprocess.run(
            ("git", "show", "cd95235d0c95ffa011eeea4fec3edf89faac9a2a:scripts/release_signature_verification.py"),
            cwd=adapter.ROOT, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            self.skipTest("Original commit is absent in this shallow checkout; frozen parser coverage still runs")
        syntax = ast.parse(result.stdout)
        function = next(node for node in syntax.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_validated_cosign_output")
        namespace = {"json": json, "MAX_JSON_BYTES": 2_097_152, "MAX_SIGNATURES": 16,
                     "SIGNATURE_TYPE": adapter.SIGNATURE_TYPE, "ReleaseSignatureError": ValueError}
        # Execute only the exact historical pure parser, not its module imports,
        # CLI, environment/source lookup, or any provider command.
        exec(compile(ast.Module(body=[function], type_ignores=[]), "original-verifier-parser", "exec"), namespace)
        parser = namespace["_validated_cosign_output"]
        kwargs = dict(repository=self.repository, reference=self.repository + "@" + self.digest,
                      digest=self.digest)
        document = self.document()
        with self.assertRaises(ValueError):
            parser(json.dumps(document).encode(), **kwargs)
        code, output, _, _ = self.invoke(self.arguments(), document)
        self.assertEqual(code, 0)
        self.assertEqual(parser(output.encode(), **kwargs), 1)
        for section, key, bad_value in (
            ("identity", "docker-reference", "docker.io/other/iip"),
            ("image", "docker-manifest-digest", "sha256:" + "a" * 64),
            (None, "type", "wrong-type"),
        ):
            altered = json.loads(output)
            selected = altered[0]["critical"]
            if section is not None:
                selected = selected[section]
            selected[key] = bad_value
            with self.assertRaises(ValueError):
                parser(json.dumps(altered).encode(), **kwargs)

    def test_all_other_claim_changes_remain_rejected(self) -> None:
        claims = (
            "registry-1.docker.io/thedevopshuman/iip", "index.docker.io.evil/thedevopshuman/iip",
            "index.docker.io:443/thedevopshuman/iip", "Index.docker.io/thedevopshuman/iip",
            "index.docker.io/other/iip", "index.docker.io/thedevopshuman/other",
            "index.docker.io/thedevopshuman/iip:v0.84.2", self.repository + "@sha256:" + "a" * 64,
        )
        documents = []
        for claim in claims:
            document = self.document()
            document[0]["critical"]["identity"]["docker-reference"] = claim
            documents.append(document)
        wrong_digest, wrong_type = self.document(), self.document()
        wrong_digest[0]["critical"]["image"]["docker-manifest-digest"] = "sha256:" + "a" * 64
        wrong_type[0]["critical"]["type"] = "untrusted"
        documents.extend((wrong_digest, wrong_type))
        for document in documents:
            code, output, errors, _ = self.invoke(self.arguments(), document)
            self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.output-invalid\n"))
            # Even a raw hostname replacement must not make the old parser
            # accept altered repository, digest or signature-type claims.
            normalized = json.dumps(document).replace("index.docker.io/", "docker.io/").encode()
            with self.assertRaises(ValueError):
                original_parser(normalized, repository=self.repository,
                                reference=self.repository + "@" + self.digest, digest=self.digest)

    def test_only_exact_original_verifier_arguments_can_start_a_process(self) -> None:
        invalid = [(), ("sign", self.repository), ("initialize",), ("version",),
                   self.arguments(digest="sha256:" + "a" * 64), self.arguments(repository="docker.io/other/iip")]
        for flag in ("--insecure-ignore-tlog=true", "--check-claims=false", "--insecure-ignore-sct=true",
                     "--allow-insecure-registry", "--key=local.pem", "--certificate-identity-regexp=.*"):
            invalid.append((*self.arguments(), flag))
        for index, replacement in ((4, adapter.IDENTITY.replace("v0.84.2", "v0.84.3")),
                                   (4, adapter.IDENTITY.replace("release.yml", "recovery.yml")),
                                   (6, "https://other.invalid"), (2, "text")):
            arguments = list(self.arguments())
            arguments[index] = replacement
            invalid.append(tuple(arguments))
        for arguments in invalid:
            code, output, errors, run = self.invoke(arguments, self.document())
            self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.arguments-invalid\n"))
            run.assert_not_called()

    def test_failed_verification_never_emits_even_plausible_claims(self) -> None:
        code, output, errors, _ = self.invoke(self.arguments(), self.document(), status=1)
        self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.verification-failed\n"))

    def test_invalid_success_json_is_minimized(self) -> None:
        for raw in (b"private provider output", b"[]", b"{}", b"[{}]", b"[null]",
                    b'[ {"critical": null} ]', b'{"version":"3.1.2","version":"3.1.2"}',
                    b"[NaN]", b"x" * (adapter.MAX_JSON_BYTES + 1),
                    json.dumps(self.document() * 17).encode()):
            code, output, errors, _ = self.invoke(self.arguments(), raw=raw)
            self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.output-invalid\n"))

    def test_version_is_exact_and_returns_only_validated_json(self) -> None:
        for field in ("gitVersion", "version"):
            for value in ("3.1.2", "v3.1.2"):
                document = {field: value, "gitCommit": "synthetic"}
                code, output, errors, run = self.invoke(("version", "--json"), document)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(json.loads(output), document)
                self.assertEqual(run.call_args.kwargs["timeout"], 25)
        code, output, errors, _ = self.invoke(("version", "--json"), {"gitVersion": "v3.1.3"})
        self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.version-mismatch\n"))

    def test_delegate_is_pinned_wrapper_with_no_ambient_authority(self) -> None:
        with patch.dict(adapter.os.environ, {"DOCKER_CONFIG": "/private/credentials", "HOME": "/private/home",
                                            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private", "COSIGN_REKOR_URL": "unsafe",
                                            "DOCKER_HOST": "tcp://untrusted.invalid:2375", "DOCKER_CONTEXT": "private",
                                            "IIP_COSIGN_DOCKER_BIN": "/unsafe/fake-docker"}):
            code, _, _, run = self.invoke(self.arguments(), self.document())
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["cwd"], adapter.ROOT)
        self.assertEqual(run.call_args.kwargs["timeout"], 170)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(set(environment), {"PATH", "DOCKER_CONFIG"})
        self.assertNotEqual(environment["DOCKER_CONFIG"], "/private/credentials")
        self.assertFalse(Path(environment["DOCKER_CONFIG"]).exists())
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_explicit_existing_local_socket_is_only_opt_in_transport(self) -> None:
        selected = "unix:///private/owned-docker.sock"
        with patch.dict(adapter.os.environ, {"IIP_RECOVERY_DOCKER_HOST": selected,
                                            "DOCKER_HOST": "tcp://ignored.invalid:2375", "DOCKER_CONTEXT": "ignored"}):
            with patch.object(adapter.Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)) as status:
                code, _, errors, run = self.invoke(self.arguments(), self.document())
        self.assertEqual((code, errors), (0, ""))
        status.assert_called_once_with()
        self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "DOCKER_CONFIG", "DOCKER_HOST"})
        self.assertEqual(run.call_args.kwargs["env"]["DOCKER_HOST"], selected)

    def test_invalid_or_non_socket_explicit_transport_never_runs(self) -> None:
        for selected in ("", "tcp://127.0.0.1:2375", "ssh://local", "http://local", "unix://host/tmp/docker.sock",
                         "unix://user:password@host/tmp/docker.sock", "unix:relative", "unix:/tmp/docker.sock",
                         "unix:///tmp/docker.sock?query", "unix:///tmp/docker.sock?", "unix:///tmp/docker.sock#",
                         "unix:///tmp/docker.sock#fragment", "unix:///tmp/docker%2esock", "unix:///tmp/docker.sock\n",
                         "unix:///tmp/space socket", "unix:///tmp/docker.sock\x7f"):
            with patch.dict(adapter.os.environ, {"IIP_RECOVERY_DOCKER_HOST": selected}):
                with patch.object(adapter.Path, "stat") as status:
                    code, output, errors, run = self.invoke(self.arguments(), self.document())
                self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.docker-host-invalid\n"))
                status.assert_not_called()
                run.assert_not_called()
        for result in (SimpleNamespace(st_mode=stat.S_IFREG), SimpleNamespace(st_mode=stat.S_IFDIR), OSError("private path")):
            with patch.dict(adapter.os.environ, {"IIP_RECOVERY_DOCKER_HOST": "unix:///private/not-a-socket"}):
                kwargs = {"side_effect": result} if isinstance(result, OSError) else {"return_value": result}
                with patch.object(adapter.Path, "stat", **kwargs):
                    code, output, errors, run = self.invoke(self.arguments(), self.document())
                self.assertEqual((code, output, errors), (2, "", "release-recovery.cosign.docker-host-invalid\n"))
                run.assert_not_called()

    def test_subprocess_failure_is_bounded_and_minimized(self) -> None:
        for error in (OSError("private path"), subprocess.TimeoutExpired("private command", 170)):
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(adapter.subprocess, "run", side_effect=error):
                with redirect_stdout(output), redirect_stderr(errors):
                    code = adapter.main(self.arguments())
            self.assertEqual((code, output.getvalue(), errors.getvalue()),
                             (2, "", "release-recovery.cosign.unavailable\n"))


if __name__ == "__main__":
    unittest.main()
