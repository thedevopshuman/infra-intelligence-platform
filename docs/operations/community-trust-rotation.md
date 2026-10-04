# Rotate community transport trust during a planned stop

**Status:** Implemented pre-release lifecycle boundary; owned local synthetic Docker gate passed on 2026-10-04

This procedure replaces the local CA and all five certificate/private-key
pairs of the [persistent community preview](community-installation.md). It
keeps tenant identity, database and other data volumes, pricing/attribution
configuration, database/Grafana passwords, and API/Collector/receiver Bearer
credentials unchanged. It is not the disposable learning setup or a Kubernetes
PKI procedure.

The CA signing key is never retained. Rotation therefore creates an entirely
new trust generation, rather than renewing one leaf under the existing CA.
The new leaf certificates have the normal 365-day lifetime. No command silently
renews certificates, changes an application's exporter, or stops/starts your
services. Plan and own the interruption.

## Before starting

Use a source checkout containing `scripts/community_trust.py`, the repository's
Python dependencies, and the same trusted local Unix-socket Docker daemon as
the installation. Use absolute, owner-only state paths with no symlinks; never
edit the protected manifests or copy private keys into shell output, tickets,
or Git. The published `learning-v0.84.0` artifact does not contain this later
persistent-installation lifecycle command.

Choose a maintenance window before expiry where possible. Allow for the
external exporter's own queues, retries, restart behavior, and intake-age
limits; neither the rotation tool nor an overlap CA bundle guarantees zero
lost telemetry. Retain your known-good encrypted backup and separate key under
your custody policy. A backup is not a reason to run old and restored copies
simultaneously.

Check the installation first, replacing the path below with the actual state
directory (the default is this checkout's `.iip/community`):

```bash
.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state status
```

The result includes `activeGeneration`, `caPath`, `caSha256`, `phase`,
`notBefore`, `notAfter`, `expiringSoon`, and `expired`; an in-progress rotation
also exposes `overlapCaPath` (otherwise `null`). With no pending rotation,
`phase` is `stable`. The warning starts 30 days before expiry. This is not a
scheduled monitor, and it does not renew or notify you automatically.

Read the returned `caPath` instead of assuming `transport/ca.crt` is current.
Before enrollment, the legacy flat directory is used. First preparation copies
it into an immutable `transport-generations/<hash>` directory and records an
atomic `transport-state.json` pointer. The legacy files remain, but the pointer
becomes authoritative. Editing the old flat files does not update the active
trust generation.

## 1. Stop, then prepare a complete new generation

Pause or otherwise account for application export before the interruption.
Remove the installation's containers while preserving its data:

```bash
.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-state down

.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state prepare

.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state status
```

All project containers must be removed, not merely stopped. Never use
`docker compose down --volumes`. Preparation validates the existing material's
structure, preserves it, and creates a new CA plus the PostgreSQL, receiver,
Collector client, Collector intake-server, and receiver-health client
certificates/private keys. It publishes a public overlap CA file under
`transport-overlap/<rotation-id>.crt` and leaves the old generation selected.

The **prepared** phase intentionally blocks serving startup. Configuration
changes and backup also remain blocked until the rotation is finalized or
cancelled, so an intermediate decision cannot be mistaken for stable state.
Do not edit `transport-state.json` to bypass these gates.

## 2. Stage public overlap trust in the external exporter

Read `overlapCaPath` from `status`. Copy only that public CA bundle to the
application exporter's protected trust configuration. It contains the old and
new CA certificates, not private keys. Keep the same Collector HTTPS endpoint
and Bearer credential. Do not install it in the operating system's global trust
store or disable hostname/certificate verification.

Reload or restart the exporter as required by its implementation. Cover every
application/exporter instance that must verify this Collector; a copied file
that the exporter has not loaded is not staged trust. This command does not
perform or verify those external changes for you.

An overlap bundle allows clients to verify either generation during this
maintenance sequence. It is not a hot-rotation or dual-serving mode; the IIP
stack is still stopped, and internal workloads switch together at explicit
startup.

## 3. Activate and explicitly start

Only after you have staged external trust:

```bash
.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state activate --external-trust-staged

.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-state up
```

`--external-trust-staged` records your assertion; it is not a check of another
machine's exporter. Activation still requires a stopped installation and a
currently valid new generation. Explicit startup projects that generation to
the database, clients, receiver, and Collector, waits for health, and records a
successful-start receipt. It does not change the stored passwords or tokens.
For recovered installations, continue obeying the existing no-build/exact-image
startup restrictions.

Repeating ordinary `up` against the exact healthy, unchanged installation is a
read-only no-op: it does not rerun the volume initializer against serving TLS
files. Its checks require the expected healthy/completed containers and
matching selected trust generation, installation/credential binding, and
operational deployment fingerprint. `up --build` and `make community-up`
require all project containers removed. For unhealthy or mismatched state,
source/configuration changes, rebuilding, or repair, explicitly run `down`
before the appropriate startup command; do not use repeated `up` as repair.

Check application exporter delivery and actual authorized metadata intake,
then verify the expected durable usage/attribution/cost or coverage result.
A green readiness check or a successful TLS handshake alone is insufficient.
Do not make an unexpected paid provider call solely to satisfy this step;
use the approved workload and validation procedure for your environment.

## 4. Finalize, then narrow external trust

After the active generation has passed your real-intake check:

```bash
.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state finalize --new-trust-verified

.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state status
```

Finalization may run while the installation is serving. It requires your
assertion and freshly rechecks that the exact containers from the
successful-start receipt still match and are healthy. A previous startup of
different containers cannot stand in for this check. The assertion is about
the **currently active generation**, including the old generation if you have
rolled back.

Finalization closes this rotation's rollback option and unblocks ordinary
configuration/backup. Now replace the external exporter's overlap bundle with
the single active CA at the returned `caPath`, reload/restart it as required,
and verify delivery/intake again. Leaving overlap trust deployed indefinitely
would keep the old issuer trusted by that client. The tool retains historical
bundles and generations for diagnosis; retained files are not active trust or
automatic revocation.

Take a fresh encrypted offline backup at your next approved backup stop after
the completed lifecycle. Preserve the corresponding exact operational
deployment and images, as required by the recovery runbook.

## Roll back before finalization

If verification fails and the old certificates are still valid, keep external
overlap trust loaded, remove the installation's containers, and select the
old generation:

```bash
.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-state down

.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state rollback --external-trust-staged

.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-state up
```

Verify real intake under that selected generation, then use the same
`finalize --new-trust-verified` command to close the rolled-back rotation. The
flag's name does not mean that the rejected new certificates became active.
After finalization, narrow the external exporter to the selected active CA and
verify again. Data written before the stop is not rolled back by a transport
change.

Rollback is not allowed once finalized or when the old certificates have
expired. Do not restore old files manually, change system clocks, or disable
TLS checks to force it. Investigate a failed new generation while the source
is safely stopped instead of replacing its installation identity.

## Cancel preparation without activation

While still **prepared** and stopped:

```bash
.venv/bin/python scripts/community_trust.py \
  --state /absolute/private/iip-state cancel
```

Cancellation ends the pending rotation without selecting the new generation.
If you already distributed overlap trust, restore the active-only CA in the
external exporter and reload it. Ordinary startup still requires the old active
certificates to be valid. Cancellation is not rollback after activation, and
cannot make expired material usable.

## Expired installations and interrupted commands

`status` can show expiry and `prepare` can use structurally valid expired source
material to create a fresh generation. Complete the same stopped activation,
external-trust, startup, intake, and finalization sequence; there is no valid
rollback to an expired source. Normal configuration validity, exact recovered
image checks, and all other startup requirements still apply. This procedure
does not bypass recovery's operational-deployment compatibility fingerprint.

Repeated commands in their corresponding completed phase are idempotent; check
`status` after an interruption before deciding the next transition. An invalid
transition fails closed. Do not delete state pointers, generations, startup
receipts, or incomplete-recovery markers to make a command proceed. Historical
material is retained with a limit of 64 generation directories and 256 history
events; preparation reserves space for the remaining lifecycle transitions.
No automatic prune or secure-erasure operation is provided. If a limit is
reached, obtain a reviewed maintenance procedure rather than deleting history
or keys manually. Retrying an already finalized command is an idempotent
completion, not a new ongoing-health or real-intake check.

## Evidence and remaining responsibilities

Run the source checks and the separate owned Docker exercise:

```bash
make verify PYTHON=.venv/bin/python
make test-community-trust PYTHON=.venv/bin/python
```

The Docker gate uses disposable owned projects and synthetic telemetry, not
customer credentials or live provider calls. CI is configured to run it after
the community recovery gate. On 2026-10-04, the final local run passed in
177.0 seconds, following an earlier 184.2-second pass. The final run additionally
confirmed that repeated ordinary `up` leaves the initializer's `StartedAt`
unchanged. Across rotation, rollback, and encrypted fresh restore, it checked:

- the new generation changed CA/leaf identities; old-only trust was rejected,
  while new-only and overlap trust worked;
- persisted and queued telemetry survived, reaching exactly six usage, cost,
  and attribution records after rollback;
- API, worker, and receiver database probes used TLS, four transport-projection
  receipts matched, and non-transport credentials/configuration stayed unchanged;
- the Grafana folder and Prometheus history remained, and encrypted fresh
  restore preserved settled generation history without a stale startup receipt.

The same candidate passed `make verify` with 1,545 tests and 73 skips, plus
console, TypeScript, Helm, and preflight checks. These are exact local synthetic
results, not evidence of a customer exporter rollout, a live Bedrock invocation,
public-v1 readiness, or production lifecycle qualification. Rerun the gates for
the actual release candidate and separately validate customer intake.

This is a planned-stop single-host mechanism, not scheduled renewal, HA/hot
rotation, external application management, password/token rotation, PKI
revocation, compromise recovery, or Kubernetes/enterprise CA integration.
Operators retain monitoring, scheduling, exporter ownership, recovery custody,
and customer validation responsibilities. See
[ADR 0159](../decisions/0159-stopped-community-transport-rotation.md),
[offline recovery](community-recovery.md), and the
[public-v1 roadmap](../roadmap/public-v1-release-plan.md).
