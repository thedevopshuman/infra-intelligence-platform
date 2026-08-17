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

The gate deliberately runs with `--network=none` and `--pull=never`. A failure
because Docker Desktop is unavailable, the image is absent, signature or digest
does not match, permissions request network/secrets/actions, a deadline expires,
or output exceeds the session limit is expected to fail closed. Provider stderr
is not surfaced.

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
plugins. Network, secrets, and actions remain denied until mediated grants exist.

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
