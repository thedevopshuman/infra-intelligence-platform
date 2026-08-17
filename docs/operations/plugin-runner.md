# Signed plugin runner operations

**Status:** Local conformance runbook

Run the complete offline gate with Docker Desktop:

```bash
make test-plugin-runner
```

The target builds the example observer image locally, resolves its immutable
image ID, creates an ephemeral Ed25519 publisher key, signs the exact artifact
descriptor, opens a bounded plugin session, and invokes the container through
the hardened runner. The result must match the observer's canonical golden
resource collection and validate against the public invocation-result schema.
The temporary image tag is removed afterward; no credential is printed or
mounted into the container.

The conformance invocation declares the Kubernetes API as a manifest upper
bound, embeds one exact read grant, and uses a protected host binding. The
container reaches only `/run/iip-mediation/request.sock`; the host test gateway
returns the provider fixture through that socket. This proves the Docker bind,
framing, path/query grant, policy, pre-egress audit, count limit, and
no-network execution together. It does not claim live customer credential or
Kubernetes interoperability.

The relay image is built from
`deploy/plugin-mediation-bridge/Dockerfile`, resolved to its immutable image ID,
and supplied explicitly to the runner. The runner uses a one-shot no-network
initializer to make the fresh volume writable, then runs the relay as UID 65532
with no capabilities, a read-only root, and fixed CPU/memory/PID/file limits.
The plugin mounts the volume read-only. A runner must never reuse this volume or
substitute a mutable relay tag.

The gate deliberately runs with `--network=none` and `--pull=never`. A failure
because Docker Desktop is unavailable, the image is absent, signature or digest
does not match, a connected invocation lacks an exact host binding, policy or
audit fails, a deadline expires, or output exceeds a limit is expected to fail
closed. Action permissions remain unsupported. Provider stderr is not surfaced.

Production deployment must place Docker/containerd access in a dedicated runner
service, configure reviewed publisher public keys, pre-pull digest-pinned images,
and export runner audit/SLO telemetry. Never mount a container runtime socket or
customer cloud/Kubernetes credentials into the API pod.

The conformance command intentionally uses the process-local implementation of
the claim state machine. A production runner must inject the PostgreSQL
operational store so claims and terminal results survive restarts. An exact
completed retry is served from storage; `plugin.request.reconciliation-required`
means a prior attempt may have produced impact and must not be automatically
retried. Restarting or deleting runner processes is not a recovery action.

The durable ledger does not by itself authorize connected or side-effecting
plugins. Read connectivity additionally requires the signed manifest, embedded
grant, protected binding, policy, audit, broker, path/query, and limit checks.
Never place an endpoint, credential reference, CA path, token, Docker socket, or
control-plane token in the grant or plugin environment. Actions remain denied.

Operators can inspect `GET /v1/plugin-invocations/{id}/status` and request
cooperative stop with `POST /v1/plugin-invocations/{id}/cancel`. The accepted
response means intent was recorded; wait for terminal `cancelled` state before
treating the container as stopped. A runner polls the durable intent at most four
times per second and kills the container without disclosing a control-plane
credential to it.

If a runner disappears and the claim remains non-terminal after its immutable
deadline, a policy-approved `platform-admin` can call
`POST /v1/plugin-invocations/{id}/reconcile`. This stores
`plugin.execution.outcome-unknown` and an audit record without replay. Do not use
database updates, pod deletion, or runner restart to bypass this workflow.
