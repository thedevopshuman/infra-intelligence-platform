# ADR 0158: Offline encrypted whole-installation community recovery

**Status:** Accepted implementation boundary

**Date:** 2026-10-04

## Context

The persistent community preview in ADR 0156 stores related facts across
PostgreSQL, the Collector queue, Prometheus, Grafana, and protected host-side
configuration. Copying only the database would omit queued telemetry, dashboard
state, credentials, transport keys, and the exact policies needed to interpret
the records. Named-volume persistence is not a recoverable backup.

The Apache-2.0 learning release under ADR 0157 remains a separate, disposable
profile. Recovery work must not turn that lesson, or an offline copy, into a
production availability, migration, or customer qualification claim.

## Decision

Add an explicitly invoked, host-side recovery command under `scripts/` with
three operations: `keygen`, `backup`, and `restore`. It composes existing
installation validation, the selected local Docker daemon, and bounded
network-disabled volume helpers. It is not a serving use case, SDK method,
agent tool, or plugin capability, and adds no provider or cloud-storage access.

- Back up only after **all** source-project containers have been removed by
  the ordinary non-destructive shutdown path. Reject volumes mounted by any
  other container and require PostgreSQL's native control metadata to report
  a clean shutdown. The tool never stops a workload automatically.
- Capture the four data volumes together: PostgreSQL, Collector queue,
  Prometheus, and Grafana. Include the installation manifest, credentials,
  transport material, and every retained immutable configuration generation.
  Regenerate projected configuration/secret volumes from that protected state
  only when the restored installation is explicitly started.
- Record actual container image IDs after successful community startup.
  Bind the backup to those image IDs, Linux OS/architecture, and a fingerprint
  of operational deployment files. Require those exact images locally at
  restoration and prohibit build/pull fallback on recovered startup. Mutable
  tags and software version strings cannot substitute for the recorded IDs.
- Encrypt the complete bounded archive with streaming AES-256-GCM and a
  separately held random 32-byte key. Authenticate the versioned envelope
  before publishing plaintext. Use owner-only files/directories, reject path
  symlinks and hard-linked inputs, and publish without replacing a destination.
  Encryption is not a secure-erasure guarantee or a key-custody service.
- Validate the authenticated manifest, exact member hashes, closed state
  layout, and strict bounded USTAR volume archives before allocating target
  volumes. Volume transport accepts regular files and directories with
  allowlisted numeric owners and ordinary permission bits only; no links,
  devices, executable archive metadata, or archive extraction API is used.
  Import validates first and requires identical archive hashes during its
  second, writing pass.
- Restore only to a never-existing protected state path and absent target
  named volumes. Rebind the new installation to the selected local daemon,
  preserve tenant/configuration/credentials, and leave it **stopped**. Require
  `--source-fenced` as an explicit operator assertion that the old installation
  cannot receive traffic or restart. Also check the old project when visible
  on the selected daemon; do not pretend this proves fencing on another host.
- Keep `.recovery-incomplete` and newly allocated state after a partial restore
  failure. Serving startup/configuration must reject that marker. Do not retry
  over partial data, remove the marker to bypass the guard, or automatically
  delete diagnostic state.
- Permit cold rescue when pricing qualifications or certificates have expired,
  but require ordinary current validation before startup. Recovery neither
  renews certificates nor approves catalog validity or organizational ownership.

The default plaintext archive cap is 8 GiB including tar overhead; the hard
cap is below 32 GiB so the authenticated envelope also fits its 32 GiB cap.
USTAR files are individually smaller than 8 GiB, sparse files are stored
densely, and member/configuration limits remain closed. Operators need space
for protected temporary plaintext copies as well as encrypted archives and
restored volumes.

## Consequences and non-claims

This supersedes the absence of an implemented community cold-copy mechanism
described in ADR 0156; it does not supersede that profile's pre-release status.
It is not an in-place or cross-version upgrade, cross-architecture migration,
online backup, scheduled/cloud backup service, logical export, incremental
backup, HA/failover controller, credential rotation, or certificate renewal.
No RPO, RTO, crash-consistency, regional disaster-recovery, provider-accounting,
or production-readiness guarantee follows from an offline roundtrip.

Unit tests cover encryption/publication, path and archive rejection, ownership,
limits, and startup fencing. The separately invoked owned Docker gate exercises
actual database/queue/backend restoration and has passed the local synthetic
roundtrip recorded in the runbook. It must be rerun for a selected release
candidate; the existence of the harness is not current-candidate evidence.
Customer recovery drills and off-host archive/key custody remain operator
responsibilities.

See the [community recovery runbook](../operations/community-recovery.md).
