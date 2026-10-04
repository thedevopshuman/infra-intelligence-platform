from __future__ import annotations

from dataclasses import replace
from http import HTTPStatus
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, Draft7Validator, FormatChecker

from iip.adapters.memory import AllowTenantPolicy
from iip.application.event_outbox_retention import (
    EventOutboxRetentionAuthorizationError, EventOutboxRetentionPolicy,
    EventOutboxRetentionService, EventOutboxRetentionStateError,
    GetEventOutboxRetentionCommand,
)
from iip.application.ports import ActorContext, EventOutboxRetentionState, PolicyDecision
from iip.bootstrap import (
    _event_outbox_retention_policy_from_env, build_local_runtime,
    build_workflow_worker_runtime_from_env,
)
from iip.surfaces.http import ApiHandler
from iip.surfaces.worker import (
    event_outbox_retention_interval_seconds, run_event_outbox_retention_pass,
)


ROOT = Path(__file__).resolve().parents[1]
TENANT = "tenant-retention"
NOW = "2026-10-04T12:00:00Z"
ACTOR = ActorContext("operator", TENANT, ("platform-admin",))
PREFIX = "IIP_EVENT_OUTBOX_RETENTION_"


class Clock:
    def now(self):
        return NOW


class Store:
    def __init__(self):
        self.calls = []
        self.state = EventOutboxRetentionState(TENANT, NOW, 6, 4, 3, 0, 3, 3)

    def evaluate_event_outbox_retention(self, tenant, now, **kwargs):
        self.calls.append((tenant, now, kwargs))
        return self.state


class RetentionServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.service = EventOutboxRetentionService(self.store, AllowTenantPolicy(), Clock())

    def test_disabled_observation_is_exact_tenant_and_read_only(self):
        report = self.service.get(GetEventOutboxRetentionCommand(ACTOR)).to_dict()
        self.assertEqual(report["spec"]["status"], "disabled")
        self.assertEqual(report["spec"]["mode"], "observe")
        self.assertEqual(self.store.calls[0][:2], (TENANT, NOW))
        self.assertIs(self.store.calls[0][2]["expire"], False)
        self.assertEqual(report["spec"]["rows"]["protected"], 3)
        self.assertNotIn("auditRef", report["spec"])
        schema = json.loads((ROOT/"contracts/schemas/event-outbox-retention-report.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
        with self.assertRaises(EventOutboxRetentionAuthorizationError):
            self.service.expire(TENANT)
        self.assertEqual(len(self.store.calls), 1)

    def test_mutation_binds_system_actor_policy_and_audit(self):
        class Policy:
            def decide(inner, actor, action, resource):
                inner.input = (actor, action, resource)
                return PolicyDecision(True, "allowed")
        policy = Policy()
        self.store.state = replace(self.store.state, expired_rows=2, remaining_eligible_rows=1,
                                   audit_ref=f"audit://{TENANT}/outbox/abc")
        service = EventOutboxRetentionService(self.store, policy, Clock(),
                                            EventOutboxRetentionPolicy(enabled=True, batch_size=2))
        report = service.expire(TENANT).to_dict()
        self.assertEqual(report["spec"]["status"], "cleanup-required")
        self.assertEqual(report["spec"]["mode"], "expire")
        self.assertEqual(policy.input[0], ActorContext("iip-event-outbox-retention", TENANT, ("system-retention",)))
        self.assertEqual(policy.input[1], "event-outbox-retention:expire")
        self.assertEqual(policy.input[2]["policyDigest"], report["spec"]["policy"]["digest"])
        self.assertTrue(self.store.calls[0][2]["expire"])

    def test_role_tenant_and_policy_denial_never_reach_storage(self):
        for actor in (replace(ACTOR, roles=()), replace(ACTOR, tenant_id="*"),
                      replace(ACTOR, actor_id="anonymous")):
            with self.subTest(actor=actor), self.assertRaises(EventOutboxRetentionAuthorizationError):
                self.service.get(GetEventOutboxRetentionCommand(actor))
        class Deny:
            def decide(self, *args):
                return PolicyDecision(False, "policy.denied")
        service = EventOutboxRetentionService(self.store, Deny(), Clock(), EventOutboxRetentionPolicy(enabled=True))
        with self.assertRaises(EventOutboxRetentionAuthorizationError):
            service.expire(TENANT)
        with self.assertRaises(EventOutboxRetentionStateError):
            service.expire("*")
        self.assertEqual(self.store.calls, [])

    def test_inconsistent_untrusted_state_is_minimized(self):
        invalid = [
            {"tenant_id": "tenant-other"}, {"evaluated_at": "2026-01-01T00:00:00Z"},
            {"stored_rows": True}, {"eligible_rows": -1}, {"published_rows": 2},
            {"protected_rows": 2}, {"expired_rows": 1}, {"remaining_eligible_rows": 2},
            {"published_rows": 7}, {"audit_ref": f"audit://{TENANT}/unexpected"},
            {"stored_rows": 2**54},
        ]
        original = self.store.state
        for changes in invalid:
            self.store.state = replace(original, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(EventOutboxRetentionStateError, "^event.outbox-retention.state-invalid$"):
                self.service.get(GetEventOutboxRetentionCommand(ACTOR))

    def test_malformed_or_unavailable_policy_never_authorizes_cleanup(self):
        for allowed in ("false", 1, None, [], False):
            class Policy:
                def decide(self, *args):
                    return SimpleNamespace(allowed=allowed)
            service = EventOutboxRetentionService(self.store, Policy(), Clock(), EventOutboxRetentionPolicy(enabled=True))
            with self.subTest(allowed=allowed), self.assertRaises(EventOutboxRetentionAuthorizationError):
                service.expire(TENANT)
        class Unavailable:
            def decide(self, *args):
                raise RuntimeError("private endpoint detail")
        service = EventOutboxRetentionService(self.store, Unavailable(), Clock(), EventOutboxRetentionPolicy(enabled=True))
        with self.assertRaisesRegex(EventOutboxRetentionAuthorizationError, "^policy.denied$"):
            service.expire(TENANT)
        self.assertEqual(self.store.calls, [])

    def test_mutation_rejects_cross_tenant_audit_and_over_batch(self):
        service = EventOutboxRetentionService(self.store, AllowTenantPolicy(), Clock(),
                                            EventOutboxRetentionPolicy(enabled=True, batch_size=1))
        for expired, audit in ((1, "audit://tenant-other/abc"), (2, f"audit://{TENANT}/abc")):
            self.store.state = replace(self.store.state, expired_rows=expired, remaining_eligible_rows=3-expired, audit_ref=audit)
            with self.assertRaises(EventOutboxRetentionStateError):
                service.expire(TENANT)

    def test_policy_bounds_and_digest_cover_every_selection(self):
        original = EventOutboxRetentionPolicy()
        for changes in ({"enabled": 1}, {"published_seconds": 2591999}, {"published_seconds": 315360001},
                        {"published_seconds": True}, {"batch_size": 0}, {"batch_size": 1001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(original, **changes)
        for changes in ({"enabled": True}, {"published_seconds": 2592001}, {"batch_size": 10}):
            self.assertNotEqual(original.digest, replace(original, **changes).digest)


class RetentionSurfaceTests(unittest.TestCase):
    def test_http_route_is_authenticated_observe_only_and_errors_are_closed(self):
        class Authenticator:
            def authenticate_bearer(self, token):
                return ACTOR
        runtime = build_local_runtime(Authenticator())
        self.addCleanup(runtime.close)
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.headers = {"authorization": "Bearer fixture-token-0123456789abcdef"}
        responses = []
        handler._json = lambda status, body: responses.append((status, body))
        handler.path = "/v1/operations/events/retention"
        handler.do_GET()
        self.assertEqual(responses[-1][0], HTTPStatus.OK)
        report = responses[-1][1]
        self.assertEqual(report["kind"], "EventOutboxRetentionReport")
        self.assertEqual(report["spec"]["status"], "disabled")
        handler.path += "?expire=true"
        handler.do_GET()
        self.assertEqual(responses[-1], (HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid"}}))
        handler.headers = {}
        handler.path = "/v1/operations/events/retention"
        handler.do_GET()
        self.assertEqual(responses[-1][0], HTTPStatus.UNAUTHORIZED)
        for error, code, status in (
            (EventOutboxRetentionAuthorizationError("private detail"), "policy.denied", HTTPStatus.FORBIDDEN),
            (EventOutboxRetentionStateError("private detail"), "event.outbox-retention.unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
        ):
            with patch.object(runtime.event_outbox_retention, "get", side_effect=error):
                handler._query_event_outbox_retention(ACTOR, "")
            self.assertEqual(responses[-1], (status, {"error": {"code": code}}))

    def test_environment_requires_explicit_duration_on_enable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_event_outbox_retention_policy_from_env().enabled)
        for values in (
            {"ENABLED": "true"}, {"ENABLED": "TRUE"}, {"ENABLED": "maybe"},
            {"PUBLISHED_SECONDS": ""}, {"PUBLISHED_SECONDS": " 2592000"},
            {"PUBLISHED_SECONDS": "2591999"}, {"PUBLISHED_SECONDS": "２５９２０００"},
            {"BATCH_SIZE": "-1"}, {"BATCH_SIZE": "true"},
        ):
            with self.subTest(values=values), patch.dict(os.environ, {PREFIX+k: v for k,v in values.items()}, clear=True), self.assertRaises(ValueError):
                _event_outbox_retention_policy_from_env()
        with patch.dict(os.environ, {PREFIX+"ENABLED": "true", PREFIX+"PUBLISHED_SECONDS": "5184000", PREFIX+"BATCH_SIZE": "8"}, clear=True):
            self.assertEqual(_event_outbox_retention_policy_from_env(), EventOutboxRetentionPolicy(True, 5184000, 8))

    def test_worker_pass_is_failure_isolated(self):
        class Service:
            def expire(self, tenant):
                if tenant == "tenant-b":
                    raise RuntimeError("secret")
                return SimpleNamespace(to_dict=lambda: {"spec": {"rows": {"expired": 2, "remainingEligible": 3}}})
        summary = run_event_outbox_retention_pass(Service(), ("tenant-a", "tenant-b"))
        self.assertEqual((summary.tenants, summary.expired_rows, summary.remaining_eligible_rows, summary.failures), (2, 2, 3, 1))
        for raw in ("59", "86401", "-1", "false", "６０", " 60"):
            with patch.dict(os.environ, {PREFIX+"INTERVAL_SECONDS": raw}, clear=True), self.assertRaises(ValueError):
                event_outbox_retention_interval_seconds()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(event_outbox_retention_interval_seconds(), 3600)

    def test_worker_rejects_incompatible_enrollment_before_composition(self):
        environment = {PREFIX+"ENABLED": "true", PREFIX+"PUBLISHED_SECONDS": "2592000"}
        for tenants in ("", "org:prod", "tenant-a,org:prod", "tenant-a,tenant-a", "-tenant"):
            with self.subTest(tenants=tenants), patch.dict(os.environ, {**environment, "IIP_WORKER_TENANTS": tenants}, clear=True), patch("iip.bootstrap._build_runtime_from_env") as compose:
                with self.assertRaisesRegex(ValueError, "^event.outbox-retention.configuration.invalid$"):
                    build_workflow_worker_runtime_from_env()
                compose.assert_not_called()
        with patch.dict(os.environ, {**environment, "IIP_WORKER_TENANTS": "tenant-a,tenant_b"}, clear=True), patch("iip.bootstrap._build_runtime_from_env") as compose:
            build_workflow_worker_runtime_from_env()
            compose.assert_called_once()
        # This feature does not silently break the broader existing enrollment
        # grammar when its destructive lifecycle remains disabled.
        with patch.dict(os.environ, {"IIP_WORKER_TENANTS": "org:prod"}, clear=True), patch("iip.bootstrap._build_runtime_from_env") as compose:
            build_workflow_worker_runtime_from_env()
            compose.assert_called_once()

    def test_helm_schema_requires_reviewed_policy_and_safe_limits(self):
        schema = json.loads((ROOT/"deploy/helm/infra-intelligence/values.schema.json").read_text())
        validator = Draft7Validator(schema["properties"]["eventOutboxRetention"])
        configured = {"enabled": True, "publishedSeconds": 2592000, "batchSize": 100, "intervalSeconds": 3600}
        validator.validate(configured)
        validator.validate({**configured, "enabled": False, "publishedSeconds": None})
        for changes in ({"publishedSeconds": None}, {"publishedSeconds": 2591999}, {"batchSize": 0}, {"intervalSeconds": 59}):
            self.assertTrue(list(validator.iter_errors({**configured, **changes})))

    @unittest.skipUnless(shutil.which("helm"), "Helm is required for live chart rendering")
    def test_helm_renders_only_explicit_duration_and_enforces_worker(self):
        command = ["helm", "template", "retention-check", str(ROOT/"deploy/helm/infra-intelligence"),
                   "--set", "database.transportSecurity.mode=insecure-local",
                   "--set", "database.existingSecret=retention-test-database"]
        defaults = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertIn('IIP_EVENT_OUTBOX_RETENTION_ENABLED: "false"', defaults)
        self.assertNotIn("IIP_EVENT_OUTBOX_RETENTION_PUBLISHED_SECONDS:", defaults)
        enable = ["--set", "eventOutboxRetention.enabled=true", "--set", "eventOutboxRetention.publishedSeconds=7776000"]
        without_worker = subprocess.run(command+enable, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(without_worker.returncode, 0)
        self.assertIn("worker.enabled must be true", without_worker.stderr)
        rendered = subprocess.run(command+enable+["--set", "worker.enabled=true", "--set", "worker.tenants[0]=tenant-retention"],
                                  capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertIn('IIP_EVENT_OUTBOX_RETENTION_ENABLED: "true"', rendered)
        self.assertIn('IIP_EVENT_OUTBOX_RETENTION_PUBLISHED_SECONDS: "7776000"', rendered)
        no_duration = subprocess.run(command+["--set", "eventOutboxRetention.enabled=true"],
                                     capture_output=True, text=True, timeout=30)
        self.assertNotEqual(no_duration.returncode, 0)
        invalid_tenant = subprocess.run(command+enable+["--set", "worker.enabled=true", "--set", "worker.tenants[0]=org:prod"],
                                       capture_output=True, text=True, timeout=30)
        self.assertNotEqual(invalid_tenant.returncode, 0)
        self.assertIn("canonical worker tenant identifiers", invalid_tenant.stderr)
