# ADR 0121: Provide privacy-minimized post-install deployment diagnostics

**Status:** Accepted
**Date:** 2026-09-06

## Context

IIP has separate pre-install configuration, external ingress, packaged
upgrade, recovery, and local planned-disruption gates. After installation, an
operator still had to inspect raw Kubernetes objects and manually correlate
rollout counts, pod health, and immutable image identity. Copying `kubectl`
output or logs into a support request would expose substantially more customer
metadata than this first diagnostic needs.

## Decision

1. Add a `DeploymentDiagnosticReport` and a read-only generator for one
   explicit Kubernetes context, namespace, Helm release, and expected image
   digest.
2. Read only server version plus selected Deployments and Pods. Never read
   Secret values, ConfigMap values, logs, events, or unselected namespaces.
3. Retain only a target binding digest, expected and observed release identity,
   aggregate rollout counts, aggregate pod signals, stable checks, and tool
   versions. Omit customer selectors and raw provider fields.
4. Treat the API as mandatory and worker/receiver Deployments as optional but
   visible when observed. Keep absence distinct from disabled or healthy.
5. Use `healthy`, `attention-required`, and `blocked`; do not call this report a
   qualification or SLO.
6. Recompute report semantics and caller-supplied target binding during
   verification. Requery the cluster only by generating a new report.

## Consequences

Operators receive one shareable first-response artifact and stable remediation
codes without granting pod execution or log access. Support can distinguish a
rollout problem, pod signal, identity mismatch, and cluster-access failure
without receiving object names or provider messages.

This command cannot prove ingress, dependency, database, identity-provider,
Collector, regional, or sustained-workload behavior. Those remain with their
own qualification boundaries.
