"""Repeated up is inspection-only; projected transport changes require down."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_docker
import community_recovery as recovery
import community_stack as stack
import community_trust as trust
from test_community_stack import installation_inputs
from test_community_trust import TrustDocker


LABELS = {
    "IIP_COMMUNITY_TRANSPORT_GENERATION": "io.iip.community.transport-generation",
    "IIP_COMMUNITY_INSTALLATION_BINDING": "io.iip.community.installation-binding",
    "IIP_COMMUNITY_DEPLOYMENT": "io.iip.community.deployment",
}


class StartDocker(TrustDocker):
    def __init__(self, values, project):
        super().__init__()
        self.project = project
        self.labels = {label: values[variable] for variable, label in LABELS.items()}
        self.labels.update({"com.docker.compose.project": project, "com.docker.compose.service": "initialize"})
        self.calls = []
        self.status_overrides = {}

    def text(self, *arguments):
        self.calls.append(arguments)
        if arguments[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            service = self.identifiers[arguments[-1]]
            return json.dumps(self.labels if service == "initialize" else {
                "com.docker.compose.project": self.project,
                "com.docker.compose.service": service,
            })
        service = self.identifiers[arguments[-1]]
        if service in self.status_overrides:
            return self.status_overrides[service]
        return super().text(*arguments)


class CommunityStartGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name).resolve() / "installation"
        stack.initialize(self.state, installation_inputs(), image="iip-community:test")
        self.values = {
            "IIP_COMMUNITY_TRANSPORT_GENERATION": "a" * 64,
            "IIP_COMMUNITY_INSTALLATION_BINDING": "b" * 64,
            "IIP_COMMUNITY_DEPLOYMENT": "c" * 64,
        }
        self.docker = StartDocker(self.values, stack.project_name(self.state))
        selected = patch.object(recovery, "RecoveryDocker", return_value=self.docker)
        selected.start()
        self.addCleanup(selected.stop)

    def guard(self, *arguments):
        return trust.guard_start(self.state, list(arguments), self.values)

    def test_fully_stopped_project_can_start_or_build(self):
        self.assertTrue(self.guard("up", "--detach", "--wait"))
        self.assertTrue(self.guard("build", "initialize"))
        self.assertEqual(self.docker.calls, [])

    def test_exact_healthy_running_project_is_read_only_success(self):
        self.docker.running = True
        before = {path.relative_to(self.state): path.read_bytes()
                  for path in self.state.rglob("*") if path.is_file()}
        self.assertFalse(self.guard("up", "--detach", "--wait"))
        self.assertFalse(self.guard("up", "--detach", "--wait"))
        after = {path.relative_to(self.state): path.read_bytes()
                 for path in self.state.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(self.docker.calls)
        self.assertTrue(all(arguments[0] == "inspect" for arguments in self.docker.calls))

    def test_running_project_cannot_build_even_when_every_binding_matches(self):
        self.docker.running = True
        with self.assertRaises(ValueError):
            self.guard("build", "initialize")

    def test_explicit_recreation_options_cannot_turn_repeated_up_into_mutation(self):
        self.docker.running = True
        for option in ("--build", "--force-recreate", "--always-recreate-deps", "--renew-anon-volumes", "-V"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.guard("up", "--detach", option)

    def test_stopped_but_unremoved_container_still_blocks_build(self):
        self.docker.running = True  # containers() includes stopped containers too.
        self.docker.identifiers = {"d" * 64: "initialize"}
        with self.assertRaises(ValueError):
            self.guard("build", "initialize")

    def test_each_changed_or_absent_initializer_binding_requires_down(self):
        self.docker.running = True
        original = dict(self.docker.labels)
        for label in LABELS.values():
            for change in ("changed", "absent"):
                with self.subTest(label=label, change=change):
                    self.docker.labels = dict(original)
                    if change == "absent":
                        del self.docker.labels[label]
                    else:
                        self.docker.labels[label] = "f" * 64
                    with self.assertRaises(ValueError):
                        self.guard("up", "--detach", "--wait")

    def test_extra_compose_labels_do_not_change_a_matching_installation(self):
        self.docker.running = True
        self.docker.labels["com.docker.compose.config-hash"] = "fixture-compose-config-hash"
        self.assertFalse(self.guard("up", "--detach"))

    def test_initializer_cannot_claim_another_project(self):
        self.docker.running = True
        self.docker.labels["com.docker.compose.project"] = "iip-community-0000000000"
        with self.assertRaises(ValueError):
            self.guard("up", "--detach")

    def test_missing_duplicate_or_unknown_service_requires_down(self):
        self.docker.running = True
        complete = dict(self.docker.identifiers)
        for scenario in ("missing", "duplicate", "unknown"):
            with self.subTest(scenario=scenario):
                self.docker.identifiers = dict(complete)
                if scenario == "missing":
                    self.docker.identifiers.pop(next(iter(complete)))
                else:
                    self.docker.identifiers["f" * 64] = "api" if scenario == "duplicate" else "unrecognized-service"
                with self.assertRaises(ValueError):
                    self.guard("up", "--detach")

    def test_running_service_health_and_completed_job_success_are_required(self):
        self.docker.running = True
        for service, status in (
            ("api", "api|running|0|unhealthy"),
            ("api", "api|exited|0|healthy"),
            ("initialize", "initialize|exited|1|"),
            ("initialize", "initialize|running|0|"),
            ("migrate", "migrate|exited|1|"),
            ("otel-collector", "otel-collector|exited|0|"),
        ):
            with self.subTest(service=service, status=status):
                self.docker.status_overrides = {service: status}
                with self.assertRaises(ValueError):
                    self.guard("up", "--detach")

    def test_malformed_initializer_labels_fail_closed(self):
        self.docker.running = True
        for labels in (None, [], "private malformed labels", {label: None for label in LABELS.values()}):
            with self.subTest(labels=labels):
                self.docker.labels = labels
                with self.assertRaises(ValueError):
                    self.guard("up", "--detach")

    def test_run_compose_does_not_execute_compose_for_exact_healthy_repeated_up(self):
        self.docker.running = True
        with patch.object(stack, "installed_environment", return_value=(self.values, stack.project_name(self.state))), \
                patch.object(community_docker, "local_docker_binding", return_value=("fixture-docker", "unix:///fixture.sock", {})), \
                patch.object(stack.subprocess, "run") as execute:
            stack.run_compose(self.state, ["up", "--detach", "--wait"])
        execute.assert_not_called()

    def test_run_compose_refuses_build_before_execution_when_containers_exist(self):
        self.docker.running = True
        with patch.object(stack, "installed_environment", return_value=(self.values, stack.project_name(self.state))), \
                patch.object(community_docker, "local_docker_binding", return_value=("fixture-docker", "unix:///fixture.sock", {})), \
                patch.object(stack.subprocess, "run") as execute:
            with self.assertRaises(ValueError):
                stack.run_compose(self.state, ["build", "initialize"])
        execute.assert_not_called()

    def test_rescue_validation_path_cannot_be_used_for_start_or_build(self):
        for command in ("up", "build"):
            with self.subTest(command=command), \
                    patch.object(stack, "installed_environment", return_value=(self.values, stack.project_name(self.state))), \
                    patch.object(community_docker, "local_docker_binding", return_value=("fixture-docker", "unix:///fixture.sock", {})), \
                    patch.object(stack.subprocess, "run") as execute:
                with self.assertRaises(ValueError):
                    stack.run_compose(self.state, [command], validate_contracts=False)
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
