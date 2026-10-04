# Offline encrypted community backup and recovery

**Status:** Implemented pre-release recovery boundary with an exercised owned local Docker roundtrip

This runbook applies only to the
[persistent single-host community preview](community-installation.md). It does
not apply to the disposable learning demo, the Helm backup profile, or an
arbitrary PostgreSQL installation. The tool never stops or starts services,
overwrites an installation, invokes a provider, or uploads data.
Use a checkout containing this recovery implementation; it is not included in
the earlier `learning-v0.84.0` source-only learning artifact.

## What the backup contains

One encrypted archive holds the related offline state together:

- PostgreSQL data, including IIP resources, evidence, investigations, usage,
  cost, attribution, findings, events, and outbox records that were persisted;
- the Collector's persistent queue;
- Prometheus and Grafana data;
- the installation manifest, retained immutable input/configuration
  generations, rendered dashboard/Collector configuration, credentials,
  certificates, and private transport keys;
- a manifest binding file hashes, source project/daemon, operational deployment
  fingerprint, and actual runtime image IDs with their OS/architecture.

Projected secret/configuration volumes are rebuilt from the protected files at
explicit startup. The archive does **not** contain its decryption key, Docker
images, source checkout, AWS credentials, or external provider/backend state.
Retain the matching operational source and exact images separately. Encryption
does not make an archive safe to publish: it contains the complete customer
installation and its credentials.

## Preconditions and limits

Use the repository Python environment and dependencies, plus a trusted local
Unix-socket Docker daemon. This operation is not the Docker-only learning
launcher. The source installation must have completed a successful startup
that recorded `runtime-images.json`; do not reconstruct that record from
mutable image tags. Keep the same operational deployment files for backup and
restore. Documentation-only changes are not a migration; changed installer,
helper, Compose, or image-building files require separate compatibility work.

The destination must use the same Linux image architecture (`amd64` or `arm64`)
as the source, with every recorded image ID already present. The tool neither
pulls an equivalent-looking tag nor rebuilds a missing image. Same-architecture
checks are necessary constraints, not proof of qualification on every host.

Use absolute paths with no symlink components. State, archive, and key parents
must be owned by the invoking user with mode `0700`; input/output files are
mode `0600`, regular, and not hard-linked. Keep keys and archives outside the
installation state and out of Git. On macOS, `/tmp` and `/var` can themselves
be symlinks; use real private paths rather than resolving user-supplied links
to bypass validation. Paths used for Docker mounts must not contain commas or
line breaks. Restore inside this checkout is allowed only beneath `.iip/`.

The default `--max-bytes` is `8589934592` (8 GiB of total plaintext, including
archive overhead). The accepted range starts at 1 MiB and ends at
`34359738329` (32 GiB minus the 39-byte encryption envelope). Set an explicit
larger limit on both backup and restore if needed; it does not waive the
following bounds:

- Each regular USTAR member must be smaller than 8 GiB. This also limits each
  embedded volume archive, not only individual database files.
- Each volume has at most 200,000 members including its root. No symlinks,
  hard links, sockets, devices, archive extensions, or privilege-bearing mode
  bits are accepted. External PostgreSQL tablespaces or a symlinked `pg_wal`
  are therefore outside this profile.
- Sparse files are copied densely and count by **logical** size. Compression,
  sparse-layout preservation, and incremental backup are not provided.
- At most 256 immutable configuration generations are retained in this format;
  state/manifest files also have bounded sizes and a closed member layout.

Allow substantial scratch space in the private archive/destination parent:
individual volume archives, the combined plaintext payload, and encrypted
output can coexist during backup; decrypted input and unpacked volume archives
coexist during restore. The size limit is not a total-disk reservation. Keep
disk encryption enabled if required by your data policy. Temporary plaintext
files are not a promise of secure erasure after failure or process termination.

## 1. Create and separately protect a key

Replace every `/absolute/private/...` path below with your own real path. The
parent directories must already exist and satisfy the protection rules.

```bash
.venv/bin/python scripts/community_recovery.py keygen \
  --key /absolute/private/iip-keys/community-backup.key
```

The command creates a new random 32-byte binary key, never prints it, and
refuses to replace an existing file. It is not a password and cannot be
recovered from an archive. Arrange a separately protected backup of the key
and a recovery drill; losing the only copy makes the archive unusable. This
tool does not manage a password vault, cloud KMS, retention, or off-host copies.

## 2. Stop and create an offline backup

Plan the collection interruption. Stop application exporters or otherwise
account for their bounded buffering; IIP cannot guarantee what external
applications retain while its endpoint is unavailable. Then remove the source
project's containers while retaining its volumes:

```bash
.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-state down

.venv/bin/python scripts/community_recovery.py backup \
  --state /absolute/private/iip-state \
  --archive /absolute/private/iip-archives/community-snapshot.iip \
  --key /absolute/private/iip-keys/community-backup.key
```

Use the actual initialized state path; the default installation is this
checkout's `.iip/community`. **Do not use `docker compose down --volumes`.**
The archive path must not already exist. Backup requires all project
containers removed, no other container mounting a data volume, and a clean
PostgreSQL shutdown. A killed database or a merely stopped-but-retained
container is not accepted. Nothing is stopped automatically to satisfy these
checks.

Success means a bounded encrypted archive was published without replacing any
file. The source is still stopped. Decide explicitly whether to resume it or
keep it fenced for recovery; taking a backup alone does not fence future
traffic. If you resume the source, subsequent data is not in this snapshot.

## 3. Fence the old source and restore to a fresh destination

Before restoring, ensure the old installation cannot receive traffic or
restart. On another host this requires your own routing, exporter, process,
and restart controls. `--source-fenced` is your assertion, not an automated
cross-host check. Never run the original and restored copies concurrently
against the same customer telemetry stream; the backup retains credentials
and identity rather than creating an unrelated tenant.

Prepare a mode-`0700` destination **parent**, but do not create the destination
state directory. The target named volumes must also be absent. Select the
intended local daemon and make the exact recorded images available through
your separately reviewed image-custody process.

```bash
.venv/bin/python scripts/community_recovery.py restore \
  --state /absolute/private/iip-restores/restored-state \
  --archive /absolute/private/iip-archives/community-snapshot.iip \
  --key /absolute/private/iip-keys/community-backup.key \
  --source-fenced
```

Restore authenticates and validates the archive, reserves a fresh state path,
creates fresh named volumes, imports the four data snapshots, and checks clean
database metadata. It binds the new state to the selected local daemon and
records source provenance. A successful restore remains **stopped**: it has
not proved application health, current pricing, certificate validity, invoice
agreement, or the ability to receive new traffic.

## 4. Revalidate before explicitly starting

Cold rescue does not require still-current price qualifications or
certificates; ordinary startup does. Revalidate using the same operational
checkout, inspect your configuration's validity, and keep the old source
fenced:

```bash
.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-restores/restored-state check

.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-restores/restored-state up

.venv/bin/python scripts/community_stack.py \
  --state /absolute/private/iip-restores/restored-state status
```

Do not add `--build` or use `make community-up` for recovered state: that Make
target requests a build. Recovered startup requires the recorded image IDs
with no build or pull fallback. Do not delete `recovery-images.json` to evade
that restriction.

Expired pricing qualifications need a reviewed, valid replacement through
the normal configuration workflow. Expired transport certificates are not
renewed by restore; the missing rotation procedure remains an operational
blocker. Never regenerate an entire installation to replace certificates or
silently generate a different database password.

After startup, verify your expected tenant records, recent sample usage,
coverage, dashboard access, Collector queue behavior, and end-to-end intake
before deliberately reconnecting traffic. Restored queued records can age out
under intake policy after a long outage; queued bytes are not a guarantee that
every record will later be accepted. Assess the observed loss/recovery window
against your own requirements instead of claiming zero RPO.

## Failure handling

Invalid protection, wrong keys, tampering, incompatible deployment/images,
in-use volumes, existing destinations, or limits fail closed. The public error
is deliberately generic and does not print customer data or credentials.

A partial restore keeps its newly created state/volumes and
`.recovery-incomplete` marker for diagnosis. Startup and configuration reject
that marker. Do not remove it, retry into the same destination, or run broad
Docker cleanup. Identify the exact failed destination and owned volumes,
preserve anything needed for diagnosis, and choose a separately reviewed
cleanup/retry procedure. There is no destructive recovery-cleanup command.

Backup failures do not publish a valid new archive. A crash may leave private
staging files; inspect only the exact operation's private directory before
removing anything. Keep the original installation and known-good backup until
the recovered instance has passed your recovery drill and retention policy.

## Verification and remaining work

`make verify PYTHON=.venv/bin/python` covers archive/crypto rejection, file
protection, state and image bindings, and recovered-startup fencing.
The separate owned Docker exercise is:

```bash
make test-community-recovery PYTHON=.venv/bin/python
```

It is not part of ordinary `make verify`. The owned local Docker gate passed
during this implementation on 2026-10-04 (95.6 seconds). It exercised one
persisted usage record plus a second queued record across offline recovery,
then observed two usage, cost, and attribution facts after explicit startup;
replay remained deduplicated. It also checked retained Grafana custom-folder
and provisioned-dashboard state, historical Prometheus data, captured image
identities, unchanged credentials/trust, and stopped source/target state before
startup. Only its test projects and volumes were removed.

Record and rerun the gate for the exact release candidate. This local
synthetic result is narrower than a customer recovery drill or a production
recovery objective, and is not a portable qualification artifact.

Scheduled/off-host custody, retention, certificate/key rotation, upgrades,
cross-version or cross-architecture migration, HA/failover, measured RPO/RTO,
and customer operational qualification remain outside this change. See
[ADR 0158](../decisions/0158-offline-encrypted-community-recovery.md) and the
[public v1 plan](../roadmap/public-v1-release-plan.md).
