# ADR 0152: Require operational-alert qualification before a private pilot

**Status:** Accepted

**Date:** 2026-09-08

## Context

ADRs 0143, 0145, and 0146 grew the customer pilot readiness preflight from
seven to nine independently owned evidence inputs. The resulting
`customer-ai-finops-design-partner-v1` aggregate cross-binds an exact
published and signed release, customer deployment, bounded workloads, planned
failure overlap, and the live AI FinOps path.

The private-pilot admission boundary now also requires the selected
operational rules to be loaded and healthy, component heartbeats to be
visible, and one firing/recovery notification route to work. ADR 0151 provides
a minimized `CustomerOperationalAlertQualificationReport` for exactly that
claim, but the v1 pilot aggregate does not consume it. A v1 report can
therefore remain internally valid under its historical semantics while
omitting evidence that current admission policy requires.

## Decision

1. Advance the pilot-readiness contract envelope to
   `iip.platform/v1alpha2` and its semantic level to
   `customer-ai-finops-design-partner-v2`. A v2
   `CustomerPilotReadinessReport` has ten ordered source artifacts; the tenth
   is a current, `qualified` `CustomerOperationalAlertQualificationReport`.
2. Require the alert report to select `ai-finops-v0`, not the narrower
   `core-v1` rule set. This binds the candidate to all documented core and AI
   FinOps operational rules and the expected API, worker, and receiver
   heartbeats.
3. Extend the protected pilot profile with the exact customer namespace,
   operational-alert profile, and operational-alert binding-set digests. Bind
   the source report to that profile and to the same clean source, application
   version, chart version, required migration, immutable control-plane image,
   cluster, and namespace as the selected customer deployment. The binding-set
   value is the SHA-256 digest of the RFC 8785/JCS-equivalent canonical JSON
   serialization of the complete alert report `spec.bindings` object.
4. Require the alert report's Prometheus target to equal the target used by
   the same-invocation AI FinOps report. Include the namespace and both alert
   profile/binding-set digests in the aggregate customer-environment-set
   digest so crossed monitoring and deployment evidence fails closed.
5. Require alert observation to start no earlier than customer deployment
   qualification. Apply the pilot profile's customer-evidence age and clock-
   skew objectives, require the alert report to be unexpired at assessment,
   and cap the aggregate validity at the alert report's `validUntil` in
   addition to all existing ceilings.
6. Retain only the alert report's file digest, qualification boundary and
   status, aggregate timestamps, stable checks, and pseudonymous binding
   digests. Do not copy endpoints, namespace, service names, rule labels,
   route or probe identifiers, receipt payloads, contacts, credentials, or
   certificate values into the pilot report.
7. Keep the assessor offline and additive. It revalidates existing artifacts
   and performs no installation, monitoring mutation, synthetic alert
   creation, provider call, traffic generation, signing, publication, pilot
   approval, or production promotion.

## Migration notes

- `customer-ai-finops-design-partner-v1` profiles and reports are historical,
  narrower preflight evidence. They cannot satisfy current private-pilot
  admission, even when their nine inputs remain individually valid.
- Do not edit or relabel a retained v1 artifact. Create a new protected v2
  profile, add `namespaceBindingDigest`, `operationalAlertProfileDigest`, and
  `operationalAlertBindingSetDigest`, and recompute its content-derived ID.
- Run the customer operational-alert qualifier after the selected customer
  deployment is qualified, using `ai-finops-v0`. Then rerun the pilot assessor
  with all ten exact source files. The new report receives a new content-
  derived ID and validity window.
- The profile/report envelope and schema identity advance to
  `iip.platform/v1alpha2`. The embedded `subject.contractsApiVersion` and the
  seven platform/customer source contracts remain `iip.platform/v1alpha1`;
  the three release-evidence contracts remain `iip.dev/v1alpha1`. Offline
  verification rejects v1 pilot envelopes or mixed v1/v2 pilot semantics
  rather than inferring or synthesizing the missing evidence.

## Consequences

- A current `design-partner-candidate` now proves that one exact deployed AI
  FinOps release also has evaluated rules, expected heartbeats, router
  readiness, and one synthetic firing/recovery route.
- Customer deployment, monitoring profile, namespace, and Prometheus evidence
  cannot be mixed across environments while still producing a credible
  aggregate.
- The claim remains deliberately narrow. Other routes and backends, silences,
  inhibition, human acknowledgement and escalation, monitoring HA, regional
  coverage, real-failure behavior, production traffic, and long-window SLOs
  remain external gates.
- `design-partner-candidate` remains evidence for a human admission decision,
  not authority to start or operate a pilot.

## Alternatives considered

- Keeping the alert report as an onboarding checklist item was rejected
  because the machine-verifiable aggregate could then contradict the current
  admission policy.
- Accepting `core-v1` was rejected because the first pilot explicitly includes
  AI economics and must observe that surface's coverage failures.
- Having the assessor create or fire a test rule was rejected because evidence
  aggregation must not acquire customer monitoring mutation authority.
