"""Opt-in, owned-loopback-registry proof of digest-selected community startup.

Requires the pinned registry image and four dependency digests already cached.
The current application is built with a unique fixture label and pushed only to
the disposable loopback registry. Its local references are removed before the
real explicit-pull command reacquires it and checks the four pinned public
dependencies. No public push or existing-stack cleanup is performed. Fixed
community ports must be free; run separately from other gates.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request

from test_community_runtime import preserved_test_directory
from test_community_stack import installation_inputs
import community_docker
import community_images
import community_recovery as recovery
import community_stack as stack


# Same official, immutable registry image as the release-publication gate.
REGISTRY_IMAGE = "registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
OWNERSHIP_LABEL = "io.iip.community-image-runtime"
PORTS = (18083, 14322, 19092, 13001)
IMAGE_VARIABLES = {
    **{service: "IIP_COMMUNITY_IMAGE" for service in recovery.SERVICES[:5]},
    "postgres": "IIP_COMMUNITY_POSTGRES_IMAGE",
    "otel-collector": "IIP_COMMUNITY_COLLECTOR_IMAGE",
    "prometheus": "IIP_COMMUNITY_PROMETHEUS_IMAGE",
    "grafana": "IIP_COMMUNITY_GRAFANA_IMAGE",
}


class _Docker:
    def __init__(self, binding: Path) -> None:
        with stack.installation_lock(binding):
            executable, endpoint, environment = community_docker.local_docker_binding(
                binding, dict(os.environ),
            )
        self.prefix = [executable, "--host", endpoint]
        self.environment = environment
        self.environment.pop("DOCKER_DEFAULT_PLATFORM", None)

    def run(self, *arguments: str, capture: bool = True, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                [*self.prefix, *arguments], cwd=stack.ROOT,
                env=dict(self.environment), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True, check=True, timeout=timeout,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RuntimeError("community image runtime gate: Docker command failed") from None
        output = result.stdout or ""
        if len(output.encode("utf-8")) > 131072:
            raise RuntimeError("community image runtime gate: oversized Docker output")
        return output.strip()

    def containers(self, project: str) -> list[str]:
        return self.run(
            "ps", "--all", "--quiet", "--no-trunc",
            "--filter", f"label=com.docker.compose.project={project}",
        ).splitlines()

    def inspect_image(self, reference: str) -> dict:
        document = json.loads(self.run(
            "image", "inspect", "--format",
            '{"id":{{json .Id}},"labels":{{json (index .Config "Labels")}},'
            '"tags":{{json .RepoTags}},"digests":{{json .RepoDigests}}}', reference,
        ))
        if not isinstance(document, dict) or not re.fullmatch(r"sha256:[a-f0-9]{64}", document.get("id", "")):
            raise RuntimeError("community image runtime gate: invalid image identity")
        return document


def _require_free_ports() -> None:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(
                    "community image runtime gate: community ports are occupied; "
                    "existing services were not stopped"
                ) from None


def _free_registry_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_registry(port: int) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/v2/", timeout=1) as response:
                if response.status == 200 and response.read(1024) == b"{}":
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError("community image runtime gate: loopback registry unavailable")


def _require_owned_image(owned: dict, fixture: str, tagged: str, immutable: str | None) -> None:
    # Docker Desktop's containerd store may expose a pulled digest in RepoTags
    # as well as RepoDigests. Accept that exact owned reference, never a prefix.
    if ((owned.get("labels") or {}).get(OWNERSHIP_LABEL) != fixture
            or any(value not in {tagged, immutable} for value in owned.get("tags") or [])
            or any(value != immutable for value in owned.get("digests") or [])):
        raise RuntimeError("fixture image references are not exclusively owned")


class CommunityImageOwnershipTests(unittest.TestCase):
    def test_accepts_exact_digest_in_repo_tags(self) -> None:
        tagged = "localhost:5000/test:fixture"
        immutable = "localhost:5000/test@sha256:" + "a" * 64
        for tags in ([tagged], [immutable], [tagged, immutable], []):
            with self.subTest(tags=tags):
                _require_owned_image({
                    "labels": {OWNERSHIP_LABEL: "fixture"},
                    "tags": tags, "digests": [immutable],
                }, "fixture", tagged, immutable)

    def test_rejects_unowned_labels_tags_and_digests(self) -> None:
        tagged = "localhost:5000/test:fixture"
        immutable = "localhost:5000/test@sha256:" + "a" * 64
        for changed in ({"labels": {}}, {"tags": [tagged + "-other"]},
                        {"digests": ["localhost:5000/test@sha256:" + "b" * 64]}):
            with self.subTest(field=next(iter(changed))):
                with self.assertRaises(RuntimeError):
                    _require_owned_image({
                        "labels": {OWNERSHIP_LABEL: "fixture"},
                        "tags": [tagged], "digests": [immutable], **changed,
                    }, "fixture", tagged, immutable)


@unittest.skipUnless(
    os.environ.get("IIP_TEST_COMMUNITY_IMAGE_RUNTIME") == "true",
    "explicit disposable Docker community image gate",
)
class CommunityImageRuntimeTests(unittest.TestCase):
    def test_exact_cached_digests_start_restart_and_fail_closed_without_registry(self) -> None:
        _require_free_ports()
        with preserved_test_directory() as temporary:
            parent = Path(temporary).resolve()
            binding = parent / "docker-binding"
            binding.mkdir(mode=0o700)
            docker = _Docker(binding)
            # Require a cached fixture before creating resources. The explicit
            # image-preparation command below also checks its public digests.
            try:
                docker.inspect_image(REGISTRY_IMAGE)
                for reference in community_images.DEFAULT_IMAGES.values():
                    docker.inspect_image(reference)
            except RuntimeError:
                raise RuntimeError(
                    "community image runtime gate: pinned registry/dependency images "
                    "must be cached before the gate"
                ) from None

            fixture = secrets.token_hex(12)
            registry_name = "iip-community-image-registry-" + fixture
            port = _free_registry_port()
            repository = f"localhost:{port}/iip-community-test"
            tagged = repository + ":" + fixture
            state, missing = parent / "installation", parent / "missing-image"
            states: list[Path] = []
            image_id: str | None = None
            immutable: str | None = None
            image_build_attempted = False
            registry_attempted = False
            credentials: dict[str, str] = {}
            credential_values: list[str] = []

            def command(selected: Path, *arguments: str) -> tuple[int, str]:
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    result = stack.main(["--state", str(selected), *arguments])
                text = output.getvalue() + error.getvalue()
                if any(value in text for value in credential_values):
                    raise AssertionError("community image runtime gate: generated credential disclosed")
                return result, text

            def successful(selected: Path, *arguments: str) -> None:
                code, _ = command(selected, *arguments)
                self.assertEqual(code, 0, f"community image runtime gate: installer {arguments[0]} failed")

            def remove_registry() -> None:
                identifiers = docker.run(
                    "ps", "--all", "--quiet", "--no-trunc", "--filter", f"name=^{registry_name}$",
                ).splitlines()
                if not identifiers:
                    return
                if len(identifiers) != 1:
                    raise RuntimeError("community image runtime gate: registry ownership mismatch")
                observed = json.loads(docker.run(
                    "inspect", "--format",
                    '{"name":{{json .Name}},"labels":{{json .Config.Labels}}}', identifiers[0],
                ))
                if (observed.get("name") != "/" + registry_name
                        or (observed.get("labels") or {}).get(OWNERSHIP_LABEL) != fixture):
                    raise RuntimeError("community image runtime gate: registry ownership mismatch")
                docker.run("rm", "--force", "--volumes", identifiers[0], capture=False)
                if docker.run("ps", "--all", "--quiet", "--filter", f"name=^{registry_name}$"):
                    raise RuntimeError("community image runtime gate: registry cleanup incomplete")

            def observed_containers() -> dict[str, tuple[str, str]]:
                project = stack.project_name(state)
                result: dict[str, tuple[str, str]] = {}
                for identifier in docker.containers(project):
                    observation = json.loads(docker.run(
                        "inspect", "--format",
                        '{"image":{{json .Image}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                        '"project":{{json (index .Config.Labels "com.docker.compose.project")}}}', identifier,
                    ))
                    service = observation.get("service")
                    self.assertEqual(observation.get("project"), project)
                    self.assertIn(service, IMAGE_VARIABLES)
                    self.assertNotIn(service, result)
                    result[service] = (identifier, observation.get("image"))
                self.assertEqual(set(result), set(IMAGE_VARIABLES))
                return result

            try:
                self.assertFalse(docker.run("ps", "--all", "--quiet", "--filter", f"name=^{registry_name}$"))
                registry_attempted = True
                docker.run(
                    "run", "--detach", "--pull", "never", "--name", registry_name,
                    "--label", f"{OWNERSHIP_LABEL}={fixture}",
                    "--publish", f"127.0.0.1:{port}:5000", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    "--pids-limit", "128", "--memory", "256m",
                    "--tmpfs", "/var/lib/registry:rw,size=1073741824", REGISTRY_IMAGE,
                )
                _wait_for_registry(port)
                image_build_attempted = True
                docker.run(
                    "build", "--label", f"{OWNERSHIP_LABEL}={fixture}",
                    "--tag", tagged, "--file", str(stack.ROOT / "Dockerfile"), str(stack.ROOT),
                    capture=False, timeout=600,
                )
                image = docker.inspect_image(tagged)
                self.assertEqual((image.get("labels") or {}).get(OWNERSHIP_LABEL), fixture)
                image_id = image["id"]
                docker.run("push", tagged, capture=False, timeout=180)
                image = docker.inspect_image(tagged)
                digests = [value for value in image.get("digests") or [] if value.startswith(repository + "@")]
                self.assertEqual(len(digests), 1, "fixture push did not record one exact local repository digest")
                immutable = community_images.require_digest_reference(digests[0])
                expected = {
                    variable: docker.inspect_image(reference)["id"]
                    for variable, reference in community_images.selected_image_references(immutable).items()
                }
                self.assertEqual(expected["IIP_COMMUNITY_IMAGE"], image_id)
                self.assertEqual(len(set(expected.values())), 5)

                stack.initialize(state, installation_inputs(), image=immutable)
                states.append(state)
                credentials = stack.read_protected(state / "credentials.json")
                credential_values.extend(credentials.values())
                original_credentials = (state / "credentials.json").read_bytes()
                self.assertFalse(docker.containers(stack.project_name(state)))

                # Prove the actual public image-preparation command, not merely
                # cache inspection. Delete only this fixture's two references;
                # never force-delete its ID, shared layers, or dependency images.
                for reference in (immutable, tagged):
                    remaining = docker.run(
                        "image", "ls", "--all", "--quiet", "--no-trunc",
                        "--filter", f"label={OWNERSHIP_LABEL}={fixture}",
                    ).splitlines()
                    if image_id not in remaining:
                        break
                    owned = docker.inspect_image(image_id)
                    tags = owned.get("tags") or []
                    digests = owned.get("digests") or []
                    _require_owned_image(owned, fixture, tagged, immutable)
                    if reference in [*tags, *digests]:
                        docker.run("image", "rm", "--no-prune", reference, capture=False)
                for reference in (immutable, tagged):
                    with self.assertRaises(RuntimeError):
                        docker.inspect_image(reference)
                successful(state, "images", "--pull")
                pulled = docker.inspect_image(immutable)
                self.assertEqual((pulled.get("labels") or {}).get(OWNERSHIP_LABEL), fixture)
                self.assertIn(immutable, pulled.get("digests") or [])
                # Use actual post-pull IDs returned by this daemon, preserving
                # containerd's index/manifest representation instead of deriving
                # an image ID from a guessed config digest.
                expected = {
                    variable: docker.inspect_image(reference)["id"]
                    for variable, reference in community_images.selected_image_references(immutable).items()
                }
                self.assertEqual(expected["IIP_COMMUNITY_IMAGE"], pulled["id"])
                self.assertEqual(len(set(expected.values())), 5)
                self.assertFalse(docker.containers(stack.project_name(state)))

                # All subsequent installer operations happen after the only
                # repository for the selected application digest is removed.
                remove_registry()
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.URLError):
                    opener.open(f"http://127.0.0.1:{port}/v2/", timeout=2)
                successful(state, "images")
                successful(state, "check")
                successful(state, "up")
                first = observed_containers()
                for service, (_, actual) in first.items():
                    self.assertEqual(actual, expected[IMAGE_VARIABLES[service]])

                # A healthy repeated up must not recreate the projector or
                # require the registry, even before the stopped restart proof.
                successful(state, "up")
                self.assertEqual(observed_containers(), first)
                request = urllib.request.Request("http://127.0.0.1:18083/v1/session", headers={
                    "Authorization": "Bearer " + credentials["apiToken"],
                })
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    session = json.load(response)
                self.assertEqual(session["metadata"], {"tenantId": "local", "actorId": "community-operator"})
                with stack.installation_lock(state):
                    count = stack.run_compose(state, [
                        "exec", "-T", "postgres", "psql", "-U", "iip", "-d", "iip",
                        "-tAc", "SELECT count(*) FROM iip.ai_usage_records",
                    ], validate_contracts=False).strip()
                self.assertEqual(count, "0")

                successful(state, "down")
                self.assertFalse(docker.containers(stack.project_name(state)))
                successful(state, "images")
                successful(state, "up")
                second = observed_containers()
                self.assertTrue({item[0] for item in first.values()}.isdisjoint(
                    item[0] for item in second.values()
                ))
                for service, (_, actual) in second.items():
                    self.assertEqual(actual, expected[IMAGE_VARIABLES[service]])
                if (state / "credentials.json").read_bytes() != original_credentials:
                    self.fail("community image runtime gate: restart changed installation credentials")

                missing_reference = repository + "@sha256:" + secrets.token_hex(32)
                stack.initialize(missing, installation_inputs(), image=missing_reference)
                states.append(missing)
                credential_values.extend(stack.read_protected(missing / "credentials.json").values())
                code, output = command(missing, "up")
                self.assertEqual(code, 2)
                self.assertIn("community.operation.failed", output)
                self.assertFalse(docker.containers(stack.project_name(missing)))
                self.assertFalse(docker.run(
                    "volume", "ls", "--quiet", "--filter",
                    f"label=com.docker.compose.project={stack.project_name(missing)}",
                ))
                self.assertFalse((missing / "runtime-images.json").exists())
            finally:
                cleanup_failed = False
                for selected in reversed(states):
                    try:
                        with stack.installation_lock(selected):
                            stack.run_compose(selected, ["down", "--volumes"], validate_contracts=False)
                        if docker.containers(stack.project_name(selected)) or docker.run(
                            "volume", "ls", "--quiet", "--filter",
                            f"label=com.docker.compose.project={stack.project_name(selected)}",
                        ):
                            raise RuntimeError("owned community resources remain")
                    except Exception:
                        cleanup_failed = True
                if registry_attempted:
                    try:
                        remove_registry()
                    except Exception:
                        cleanup_failed = True
                if image_build_attempted:
                    try:
                        # Only a uniquely labelled image built by this test may
                        # lose its exact references. No force/prune/global cache
                        # operation is permitted, even if cleanup is incomplete.
                        selected = set(docker.run(
                            "image", "ls", "--all", "--quiet", "--no-trunc",
                            "--filter", f"label={OWNERSHIP_LABEL}={fixture}",
                        ).splitlines())
                        for identifier in selected:
                            owned = docker.inspect_image(identifier)
                            _require_owned_image(owned, fixture, tagged, immutable)
                            tags = owned.get("tags") or []
                            digests = owned.get("digests") or []
                            # Docker may remove its matching tag when deleting a
                            # repository digest. Re-read the remaining references
                            # after each removal rather than assuming either
                            # engine's image-store implementation.
                            for reference in [*digests, *tags]:
                                remaining = docker.run(
                                    "image", "ls", "--all", "--quiet", "--no-trunc",
                                    "--filter", f"label={OWNERSHIP_LABEL}={fixture}",
                                ).splitlines()
                                if identifier not in remaining:
                                    break
                                current = docker.inspect_image(identifier)
                                if reference in [*(current.get("tags") or []), *(current.get("digests") or [])]:
                                    docker.run("image", "rm", "--no-prune", reference, capture=False)
                            remaining = docker.run(
                                "image", "ls", "--all", "--quiet", "--no-trunc",
                                "--filter", f"label={OWNERSHIP_LABEL}={fixture}",
                            ).splitlines()
                            if identifier in remaining:
                                current = docker.inspect_image(identifier)
                                if current.get("tags") or current.get("digests"):
                                    raise RuntimeError("fixture image references remain")
                                docker.run("image", "rm", "--no-prune", identifier, capture=False)
                        if docker.run("image", "ls", "--all", "--quiet", "--filter", f"label={OWNERSHIP_LABEL}={fixture}"):
                            raise RuntimeError("fixture images remain")
                    except Exception:
                        cleanup_failed = True
                if cleanup_failed:
                    raise RuntimeError(
                        "community image runtime gate: owned-resource cleanup incomplete; "
                        "protected state retained for diagnosis"
                    ) from None


if __name__ == "__main__":
    unittest.main()
