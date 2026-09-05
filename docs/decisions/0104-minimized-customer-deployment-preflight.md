# ADR 0104: Minimize and separate customer deployment preflight evidence

**Status:** Accepted
**Date:** 2026-09-05

## Context

The strict Helm values schema and render guards reject individual unsafe
combinations, while local compatibility and release reports prove the shipped
adapters and package in disposable fixtures. Neither tells an operator, before
installation, whether one production-shaped values set covers the complete
baseline or whether its exact Secret keys, ConfigMaps, and backup claim exist
in the intended namespace. Copying rendered manifests or `kubectl get secret
-o json` into support evidence would retain endpoints, topology, tenant
enrollment, and secret material. Treating a successful static render as
customer certification would overstate what repository-controlled evidence
can prove.

## Decision

1. Define two closed pre-install profiles: `production-core-v1` and
   `production-ai-finops-v0`.
2. Render a sanitized, non-secret deployment profile from the same effective
   Helm values used for installation. This profile carries only modes, flags,
   counts, immutable image identity, and referenced Kubernetes object names and
   keys already present in the workload manifests.
3. Keep static and explicit-context cluster execution distinct. Static success
   is `configuration-ready`; only a cluster run that observes the namespace and
   every exact dependency is `install-ready`.
4. Retain only digests, counts, tool/platform versions, stable result codes,
   and closed check identities in `CustomerDeploymentPreflightReport`.
   Kubernetes context, namespace, object names, endpoints, CIDRs, tenant IDs,
   values paths, raw configuration, and secret values never enter the report.
5. Invoke `kubectl get` for each exact named Secret with a bounded Go template
   that emits only UID, resource version, and data-key names. Kubernetes and
   `kubectl` necessarily handle the Secret object, but no data value crosses
   the subprocess boundary into the preflight process or retained report.
6. Permanently label the result `pre-install-only` and enumerate customer
   interoperability, release signing, database resilience, and workload/SLO
   evidence that remains outside the report.
7. Bind retained evidence to the source revision, dirty state, ordered values
   digest, sanitized configuration digest, dependency-reference digest, and—
   for live runs—pseudonymous namespace and cluster digests.

## Consequences

Operators get an executable answer before applying resources, and support can
compare exact configuration generations without receiving customer topology or
credentials. Missing dependencies and keys fail closed with stable codes. The
sanitized profile ConfigMap also makes the non-secret effective deployment
shape inspectable after installation.

The report cannot prove OIDC browser behavior, policy-bundle correctness,
credential issuance, certificate rotation, Collector delivery, PostgreSQL HA
or disaster recovery, provider behavior, pricing authority, or workload SLOs.
Those remain separate environment-specific gates; preflight success must never
be marketed as production certification.
