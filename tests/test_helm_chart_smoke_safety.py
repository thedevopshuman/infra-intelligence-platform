from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import test_helm_chart_smoke as smoke

# Keep this test module's basename distinct from the executable smoke harness:
# older operational modules add scripts/ to the discovery import search path.

RUN_ID = "a" * 32
IMAGE = "docker.io/thedevopshuman/iip@sha256:" + "b" * 64


def configuration() -> dict:
    return {
        "contexts": [{"name": "kind-iip-dev", "context": {"cluster": "kind-iip-dev", "user": "kind-iip-dev"}}],
        "clusters": [{"name": "kind-iip-dev", "cluster": {
            "server": "https://127.0.0.1:6443", "certificate-authority-data": "fixture-ca",
        }}],
        "users": [{"name": "kind-iip-dev", "user": {
            "client-certificate-data": "fixture-certificate", "client-key-data": "fixture-key",
        }}],
    }


class HelmChartSmokeTests(unittest.TestCase):
    def test_only_explicit_approved_immutable_image_is_accepted(self) -> None:
        self.assertEqual(smoke.validate_image(IMAGE), ("docker.io/thedevopshuman/iip", "sha256:" + "b" * 64))
        for image in ("thedevopshuman/iip:latest", "docker.io/thedevopshuman/iip:0.84.2",
                      IMAGE.replace("iip@", "iip-bridge@"), IMAGE + "\n", IMAGE.replace("b", "B")):
            with self.subTest(image=image), self.assertRaises(smoke.SmokeError):
                smoke.validate_image(image)

    def test_only_loopback_tls_static_kind_credentials_are_accepted(self) -> None:
        self.assertEqual(smoke.validate_context("kind-iip-dev", configuration()), ("127.0.0.1", 6443))
        for server in ("https://example.com:6443", "http://127.0.0.1:6443", "https://localhost:6443",
                       "https://127.0.0.1:6443/path", "https://name:secret@127.0.0.1:6443",
                       "https://127.0.0.1:6443?query=1", "https://127.0.0.1"):
            altered = configuration()
            altered["clusters"][0]["cluster"]["server"] = server
            with self.subTest(server=server), self.assertRaises(smoke.SmokeError):
                smoke.validate_context("kind-iip-dev", altered)
        for key, value in (("insecure-skip-tls-verify", True), ("proxy-url", "http://proxy")):
            altered = configuration()
            altered["clusters"][0]["cluster"][key] = value
            with self.subTest(key=key), self.assertRaises(smoke.SmokeError):
                smoke.validate_context("kind-iip-dev", altered)
        for user in ({"exec": {"command": "untrusted"}}, {"token": "secret"},
                     {"auth-provider": {}}, {"client-certificate-data": "", "client-key-data": ""}):
            altered = configuration()
            altered["users"][0]["user"] = user
            with self.subTest(user=user), self.assertRaises(smoke.SmokeError):
                smoke.validate_context("kind-iip-dev", altered)

    def test_preflight_rejects_unsafe_context_without_commands(self) -> None:
        with patch.object(smoke, "run") as command, self.assertRaises(smoke.SmokeError):
            smoke.local_preflight("production", Path("/tmp"), None)
        command.assert_not_called()

    def test_preflight_rejects_remote_docker_without_kind_or_mutation(self) -> None:
        with patch.object(smoke, "run", return_value=json.dumps(configuration())) as command:
            with self.assertRaisesRegex(smoke.SmokeError, "local-unix-socket-required"):
                smoke.local_preflight("kind-iip-dev", Path("/tmp"), "tcp://127.0.0.1:2375")
        self.assertEqual(command.call_count, 1)

    def test_preflight_binds_local_container_labels_api_port_and_nodes(self) -> None:
        inspection = {
            "Name": "/iip-dev-control-plane", "State": {"Running": True},
            "Config": {"Labels": {"io.x-k8s.kind.cluster": "iip-dev", "io.x-k8s.kind.role": "control-plane"}},
            "NetworkSettings": {"Ports": {"6443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6443"}]}},
        }
        outputs = [json.dumps(configuration()), "iip-dev-control-plane\n", json.dumps(inspection),
                   json.dumps({"items": [{"metadata": {"name": "iip-dev-control-plane"}}]})]
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke, "run", side_effect=outputs):
            commands = smoke.local_preflight("kind-iip-dev", Path(directory), "unix:///tmp/docker.sock")
            saved = Path(directory) / "kubeconfig.json"
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(saved.read_text()), configuration())
            self.assertIn(str(saved), commands)
            self.assertIn("kind-iip-dev", commands)
        for mutate in ("label", "port", "stopped", "nodes"):
            invalid = copy.deepcopy(inspection)
            nodes = {"items": [{"metadata": {"name": "iip-dev-control-plane"}}]}
            if mutate == "label":
                invalid["Config"]["Labels"]["io.x-k8s.kind.cluster"] = "different"
            elif mutate == "port":
                invalid["NetworkSettings"]["Ports"]["6443/tcp"][0]["HostPort"] = "6444"
            elif mutate == "stopped":
                invalid["State"]["Running"] = False
            else:
                nodes["items"][0]["metadata"]["name"] = "different-node"
            outputs = [json.dumps(configuration()), "iip-dev-control-plane\n", json.dumps(invalid), json.dumps(nodes)]
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                with patch.object(smoke, "run", side_effect=outputs) as command, self.assertRaises(smoke.SmokeError):
                    smoke.local_preflight("kind-iip-dev", Path(directory), "unix:///tmp/docker.sock")
                self.assertFalse(any("create" in item.args[0] for item in command.call_args_list))

    def test_namespace_reservation_creates_once_never_adopts_or_deletes(self) -> None:
        name = "iip-helm-smoke-" + RUN_ID
        resource = {"metadata": {"name": name, "uid": "owned-uid", "labels": {smoke.OWNER_LABEL: RUN_ID}}}
        with patch.object(smoke, "run", return_value=json.dumps(resource)) as command:
            self.assertEqual(smoke.reserve_namespace(["kubectl"], RUN_ID), (name, "owned-uid"))
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0], ["kubectl", "create", "-f", "-", "-o", "json"])
        with patch.object(smoke, "run", side_effect=smoke.SmokeError("collision")) as command:
            with self.assertRaises(smoke.SmokeError):
                smoke.reserve_namespace(["kubectl"], RUN_ID)
        command.assert_called_once()

    def test_namespace_cleanup_uses_uid_and_resource_version_preconditions(self) -> None:
        name = "iip-helm-smoke-" + RUN_ID
        resource = {"metadata": {"name": name, "uid": "owned-uid", "resourceVersion": "123",
                                  "labels": {smoke.OWNER_LABEL: RUN_ID}}}
        with patch.object(smoke, "run", side_effect=[json.dumps(resource), ""]) as command:
            smoke.cleanup_namespace(["kubectl", "--context", "kind-iip-dev"], name, "owned-uid", RUN_ID)
        delete = command.call_args_list[1]
        self.assertEqual(delete.args[0][-5:], ["delete", "--raw", f"/api/v1/namespaces/{name}", "-f", "-"])
        self.assertEqual(json.loads(delete.kwargs["data"])["preconditions"], {"uid": "owned-uid", "resourceVersion": "123"})

    def test_namespace_cleanup_refuses_replacement_or_changed_ownership(self) -> None:
        name = "iip-helm-smoke-" + RUN_ID
        for metadata in ({"uid": "different-uid", "labels": {smoke.OWNER_LABEL: RUN_ID}, "resourceVersion": "1"},
                         {"uid": "owned-uid", "labels": {smoke.OWNER_LABEL: "different"}, "resourceVersion": "1"},
                         {"uid": "owned-uid", "labels": {smoke.OWNER_LABEL: RUN_ID}}):
            with self.subTest(metadata=metadata), patch.object(smoke, "run", return_value=json.dumps({"metadata": metadata})) as command:
                with self.assertRaises(smoke.SmokeError):
                    smoke.cleanup_namespace(["kubectl"], name, "owned-uid", RUN_ID)
                command.assert_called_once()
        with patch.object(smoke, "run") as command, self.assertRaises(smoke.SmokeError):
            smoke.cleanup_namespace(["kubectl"], "default", "uid", RUN_ID)
        command.assert_not_called()

    def test_command_failures_do_not_retain_or_emit_provider_output(self) -> None:
        result = subprocess.CompletedProcess(["kubectl"], 1, "raw-secret", "raw-provider-error")
        with patch.object(smoke.subprocess, "run", return_value=result) as command:
            with self.assertRaisesRegex(smoke.SmokeError, "^helm-smoke.example.failed$"):
                smoke.run(["kubectl"], stage="example", data="stdin-secret")
        self.assertTrue(command.call_args.kwargs["capture_output"])
        self.assertEqual(command.call_args.kwargs["timeout"], 60)
        with patch.object(smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired("kubectl", 1, output="secret")):
            with self.assertRaisesRegex(smoke.SmokeError, "^helm-smoke.example.unavailable-or-timeout$"):
                smoke.run(["kubectl"], stage="example")

    def test_fixture_is_namespaced_ephemeral_digest_pinned_and_secret_backed(self) -> None:
        resources = smoke.postgres_fixture()["items"]
        self.assertEqual([item["kind"] for item in resources], ["Deployment", "Service"])
        pod = resources[0]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["volumes"][0], {"name": "data", "emptyDir": {}})
        container = pod["containers"][0]
        self.assertEqual(container["image"], smoke.POSTGRES_IMAGE)
        password = [item for item in container["env"] if item["name"] == "POSTGRES_PASSWORD"][0]
        self.assertIn("secretKeyRef", password["valueFrom"])
        self.assertNotIn("value", password)
        self.assertIn("PGSSLMODE=verify-full", container["readinessProbe"]["exec"]["command"][-1])


if __name__ == "__main__":
    unittest.main()
