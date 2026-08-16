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
