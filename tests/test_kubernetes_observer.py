from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "examples" / "kubernetes-observer"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(PLUGIN / "src"))

import validate_schemas  # noqa: E402
from iip.domain.models import Resource  # noqa: E402
from kubernetes_observer import collect  # noqa: E402
from kubernetes_observer.__main__ import main as observer_main  # noqa: E402
from kubernetes_observer.live import (  # noqa: E402
    LiveCollectionError,
    ResourceStream,
    WatchExpired,
    _watch_stream,
    cursor_checkpoint,
    list_objects,
    resource_streams,
    watch_then_list_objects,
)


def fixture(name: str) -> dict:
    return json.loads((PLUGIN / "fixtures" / name).read_text(encoding="utf-8"))


class KubernetesObserverConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = fixture("collection-request.json")
        self.objects = fixture("kubernetes-list.json")
        self.expected = fixture("expected-result.json")
        self.result_schema = validate_schemas.load_json(
            ROOT / "contracts" / "schemas" / "resource-collection-result.schema.json",
            [],
        )

    def result(self, request: dict | None = None, objects: dict | None = None) -> dict:
        return collect(request or self.request, objects or self.objects).to_dict()

    def assert_result_schema_valid(self, result: dict) -> None:
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                self.result_schema,
                result,
                label="Kubernetes observer result",
            ),
            [],
        )

    def test_fixture_matches_the_deterministic_golden_result(self) -> None:
        result = self.result()

        self.assertEqual(result, self.expected)
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                validate_schemas.load_json(
                    ROOT / "contracts" / "schemas" / "resource-collection-request.schema.json",
                    [],
                ),
                self.request,
                label="Kubernetes observer request",
            ),
            [],
        )
        self.assert_result_schema_valid(result)
        for observation in result["spec"]["observations"]:
            Resource.from_dict(observation)

    def test_output_is_independent_of_provider_list_order(self) -> None:
        reversed_objects = copy.deepcopy(self.objects)
        reversed_objects["items"].reverse()

        self.assertEqual(self.result(objects=reversed_objects), self.expected)

    def test_scope_tenancy_and_reconciliation_order_are_propagated(self) -> None:
        result = self.result()
        observations = result["spec"]["observations"]
        completion = result["spec"]["completion"]

        self.assertEqual(completion["resourceCount"], len(observations))
        self.assertEqual(completion["nextSequence"], 109)
        self.assertEqual(completion["status"], "complete")
        self.assertNotIn("reasonCode", completion)
        self.assertEqual(
            [item["metadata"]["observation"]["sequence"] for item in observations],
            list(range(100, 109)),
        )
        for item in observations:
            cursor = item["metadata"]["observation"]
            self.assertEqual(item["metadata"]["tenantId"], "local")
            self.assertEqual(cursor["sourceId"], "kubernetes-local")
            self.assertEqual(cursor["streamId"], self.request["spec"]["streamId"])
            self.assertEqual(cursor["mode"], "reconciliation")
            self.assertEqual(cursor["snapshotId"], self.request["spec"]["snapshotId"])
            self.assertNotIn("checkpoint", cursor)

    def test_secret_values_and_out_of_scope_objects_do_not_escape(self) -> None:
        encoded = json.dumps(self.result(), sort_keys=True)
        external_ids = {
            item["spec"]["externalId"]
            for item in self.result()["spec"]["observations"]
        }

        self.assertNotIn("must-not-escape", encoded)
        self.assertNotIn("c2hvdWxkLW5ldmVyLWVzY2FwZQ==", encoded)
        self.assertNotIn("api-credentials", encoded)
        self.assertNotIn("api-tls", encoded)
        self.assertNotIn("kube-system", encoded)
        self.assertNotIn("cluster-local/kube-system/controller", external_ids)

    def test_provider_uid_is_retained_as_a_safe_action_precondition(self) -> None:
        objects = copy.deepcopy(self.objects)
        deployment = next(
            item for item in objects["items"] if item.get("kind") == "Deployment"
        )
        deployment["metadata"]["uid"] = "provider-deployment-uid"

        result = self.result(objects=objects)
        normalized = next(
            item
            for item in result["spec"]["observations"]
            if item["spec"]["type"] == "apps/deployment"
        )

        self.assertEqual(
            normalized["spec"]["attributes"]["providerUid"],
            "provider-deployment-uid",
        )

    def test_unsafe_provider_uid_is_not_emitted(self) -> None:
        objects = copy.deepcopy(self.objects)
        deployment = next(
            item for item in objects["items"] if item.get("kind") == "Deployment"
        )
        deployment["metadata"]["uid"] = "provider-uid\nforged"

        result = self.result(objects=objects)
        normalized = next(
            item
            for item in result["spec"]["observations"]
            if item["spec"]["type"] == "apps/deployment"
        )

        self.assertNotIn("providerUid", normalized["spec"]["attributes"])

    def test_relationships_reference_resources_in_the_same_result(self) -> None:
        observations = self.result()["spec"]["observations"]
        resource_uids = {item["metadata"]["uid"] for item in observations}
        relationship_types = set()
        for item in observations:
            for relationship in item["spec"]["relationships"]:
                relationship_types.add(relationship["type"])
                self.assertIn(relationship["target"], resource_uids)

        self.assertEqual(
            relationship_types,
            {"contains", "owns", "reads_from", "routes_to", "runs_on"},
        )

    def test_resource_limit_fails_without_checkpoint_or_partial_observations(self) -> None:
        request = copy.deepcopy(self.request)
        request["spec"]["limits"]["maxResources"] = 8

        result = self.result(request=request)
        completion = result["spec"]["completion"]

        self.assert_result_schema_valid(result)
        self.assertEqual(completion["status"], "failed")
        self.assertEqual(completion["reasonCode"], "collector.resource_limit_exceeded")
        self.assertNotIn("checkpoint", completion)
        self.assertEqual(completion["resourceCount"], 0)

    def test_output_limit_fails_closed(self) -> None:
        request = copy.deepcopy(self.request)
        request["spec"]["limits"]["maxOutputBytes"] = 1024

        result = self.result(request=request)

        self.assert_result_schema_valid(result)
        self.assertEqual(
            result["spec"]["completion"]["reasonCode"],
            "collector.output_limit_exceeded",
        )
        self.assertEqual(result["spec"]["observations"], [])
        self.assertLessEqual(
            len(json.dumps(result, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            1024,
        )

    def test_sequence_space_exhaustion_fails_closed(self) -> None:
        request = copy.deepcopy(self.request)
        request["spec"]["startSequence"] = 9007199254740983

        result = self.result(request=request)

        self.assert_result_schema_valid(result)
        self.assertEqual(
            result["spec"]["completion"]["reasonCode"],
            "collector.sequence_exhausted",
        )
        self.assertEqual(result["spec"]["observations"], [])

    def test_missing_checkpoint_and_malformed_objects_use_stable_codes(self) -> None:
        missing_checkpoint = copy.deepcopy(self.objects)
        del missing_checkpoint["metadata"]["resourceVersion"]
        malformed = copy.deepcopy(self.objects)
        malformed["items"].append({"apiVersion": "v1", "kind": "Pod"})

        missing_checkpoint_result = self.result(objects=missing_checkpoint)
        malformed_result = self.result(objects=malformed)

        self.assert_result_schema_valid(missing_checkpoint_result)
        self.assert_result_schema_valid(malformed_result)
        self.assertEqual(
            missing_checkpoint_result["spec"]["completion"]["reasonCode"],
            "collector.checkpoint_missing",
        )
        self.assertEqual(
            malformed_result["spec"]["completion"]["reasonCode"],
            "collector.object_invalid",
        )

    def test_cli_emits_the_golden_result(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = observer_main(
                [
                    "--request",
                    str(PLUGIN / "fixtures" / "collection-request.json"),
                    "--objects",
                    str(PLUGIN / "fixtures" / "kubernetes-list.json"),
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), self.expected)

    def test_live_transport_requires_explicit_configuration_and_builds_checkpoint(self) -> None:
        provider_document = {
            "apiVersion": "v1",
            "kind": "List",
            "metadata": {"resourceVersion": "5000"},
            "items": [],
        }
        with TemporaryDirectory() as directory:
            kubeconfig = Path(directory) / "config"
            kubeconfig.write_text("development fixture", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(provider_document), stderr=""
            )
            with patch("kubernetes_observer.live.subprocess.run", return_value=completed) as run:
                result = list_objects(context="kind-iip-dev", kubeconfig=kubeconfig)

        self.assertRegex(
            result["metadata"]["resourceVersion"],
            r"^composite-sha256:[a-f0-9]{64}$",
        )
        self.assertEqual(len(result["metadata"]["providerCursors"]), 10)
        self.assertEqual(run.call_count, 10)
        command = run.call_args.args[0]
        self.assertIn("--context", command)
        self.assertIn("kind-iip-dev", command)
        self.assertIn("--kubeconfig", command)

    def test_live_transport_maps_provider_errors_to_stable_code(self) -> None:
        with TemporaryDirectory() as directory:
            kubeconfig = Path(directory) / "config"
            kubeconfig.write_text("development fixture", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="credential text must not escape"
            )
            with patch("kubernetes_observer.live.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(
                    LiveCollectionError, "collector.live.provider_error"
                ):
                    list_objects(context="kind-iip-dev", kubeconfig=kubeconfig)

    def test_watch_410_is_classified_without_exposing_provider_text(self) -> None:
        event = {
            "type": "ERROR",
            "object": {
                "apiVersion": "v1",
                "kind": "Status",
                "code": 410,
                "message": "provider history and credential detail",
            },
        }
        stream = ResourceStream(
            "pods",
            "/api/v1/namespaces/default/pods",
            "v1",
            "Pod",
            "default",
        )
        with TemporaryDirectory() as directory:
            kubeconfig = Path(directory) / "config"
            kubeconfig.write_text("development fixture", encoding="utf-8")
            responses = (
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=json.dumps(event), stderr=""
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="Error from server (Gone): provider history detail",
                ),
            )
            for completed in responses:
                with self.subTest(returncode=completed.returncode):
                    with patch(
                        "kubernetes_observer.live.subprocess.run",
                        return_value=completed,
                    ):
                        with self.assertRaisesRegex(WatchExpired, stream.key):
                            _watch_stream(
                                stream,
                                "1",
                                context="kind-iip-dev",
                                kubeconfig=kubeconfig,
                                timeout_seconds=1,
                            )

    def test_resume_cycle_relists_the_full_scope_after_watch_recovery(self) -> None:
        streams = resource_streams(("default",))
        cursors = {stream.key: str(index + 1) for index, stream in enumerate(streams)}
        scope_digest = "sha256:" + "a" * 64
        resume = {
            "checkpoint": cursor_checkpoint(
                cluster_id="cluster-local",
                scope_digest=scope_digest,
                provider_cursors=cursors,
            ),
            "providerCursors": cursors,
        }
        fresh = {"kind": "List", "metadata": {"resourceVersion": "fresh"}, "items": []}
        with TemporaryDirectory() as directory:
            kubeconfig = Path(directory) / "config"
            kubeconfig.write_text("development fixture", encoding="utf-8")
            def watch_side_effect(stream, cursor, **kwargs):
                if stream.argument == "pods":
                    raise WatchExpired(stream.key)
                return False

            with patch(
                "kubernetes_observer.live._watch_stream",
                side_effect=watch_side_effect,
            ) as watch:
                with patch(
                    "kubernetes_observer.live.list_objects", return_value=fresh
                ) as relist:
                    result = watch_then_list_objects(
                        context="kind-iip-dev",
                        kubeconfig=kubeconfig,
                        namespaces=("default",),
                        cluster_id="cluster-local",
                        scope_digest=scope_digest,
                        resume=resume,
                        timeout_seconds=1,
                    )

        self.assertIs(result, fresh)
        self.assertEqual(watch.call_count, len(streams))
        relist.assert_called_once()

    def test_image_pull_failure_is_normalized_without_provider_message_text(self) -> None:
        objects = copy.deepcopy(self.objects)
        pod = next(item for item in objects["items"] if item.get("kind") == "Pod")
        pod["status"] = {
            "phase": "Pending",
            "containerStatuses": [
                {
                    "name": "api",
                    "ready": False,
                    "state": {
                        "waiting": {
                            "reason": "ImagePullBackOff",
                            "message": "provider-specific registry credential text",
                        }
                    },
                }
            ],
        }

        result = self.result(objects=objects)
        normalized = next(
            item
            for item in result["spec"]["observations"]
            if item["spec"]["type"] == "core/pod"
            and item["spec"]["displayName"] == pod["metadata"]["name"]
        )

        self.assertEqual(normalized["status"]["health"], "unhealthy")
        self.assertEqual(
            normalized["spec"]["attributes"]["waitingReason"],
            "ImagePullBackOff",
        )
        self.assertNotIn("provider-specific", json.dumps(normalized))

    def test_rbac_is_read_only_and_excludes_secrets(self) -> None:
        rbac = (PLUGIN / "deploy" / "rbac.yaml").read_text(encoding="utf-8")

        self.assertIn("automountServiceAccountToken: false", rbac)
        self.assertNotIn('"secrets"', rbac)
        self.assertNotIn('"create"', rbac)
        self.assertNotIn('"update"', rbac)
        self.assertNotIn('"patch"', rbac)
        self.assertNotIn('"delete"', rbac)
        self.assertEqual(rbac.count('verbs: ["get", "list", "watch"]'), 4)


if __name__ == "__main__":
    unittest.main()
