# Signed plugin runtime

**Status:** Accepted first isolation profile

Plugins remain untrusted even when their publisher signature is valid. The
platform verifies trust and authority in this order:

1. resolve an explicitly installed publisher/key trust root;
2. verify the `v2` Ed25519 signature over the complete unsigned manifest digest,
   plugin identity, protocol, immutable image reference, and digest;
3. bind the manifest digest, actor, tenant, capability token digest, declared
   method, deadline, and limits to an active `PluginSession`;
4. validate any read or action-proposal grant against the signed manifest,
   deadline, and limits without performing egress or creating a proposal; bind
   read grants additionally to protected endpoint/credential configuration;
5. atomically claim the tenant/request ID against the persisted session and
   canonical invocation digest before the plugin can use the bound handler;
6. start the already-present image by immutable digest with Docker networking
   disabled, no inherited plugin environment or credentials, and at most one
   fresh invocation-local mediation-socket volume shared through the
   digest-pinned, no-network, non-root relay;
7. mediate each allowed JSON `GET` on the host through an exact broker lease and
   direct no-redirect TLS request, returning no provider headers or credentials;
8. for an action request, derive expiry and idempotency, record audit intent,
   and enter only the ordinary governed proposal workflow—never approval or
   execution;
9. enforce read-only root, non-root UID, dropped capabilities,
   `no-new-privileges`, CPU, memory/swap, PID, file-descriptor, temporary storage,
   wall-time, input, stdout, and stderr limits;
10. wrap one bounded JSON object in a host-created `PluginInvocationResult` and
   pass it to the owning contract validator before application use.

The Docker client uses `--pull=never`. Artifact acquisition and signature review
are separate installation actions; invocation cannot contact a registry or
substitute another tag. Container exit, timeout, oversized output, malformed
JSON, signature failure, scope mismatch, and unknown methods become stable error
codes without stderr or provider text.

The PostgreSQL ledger serializes claims against the persisted session, enforces
request counts across replicas, stores terminal results, replays those results
without re-execution, and leaves crash-ambiguous claims closed for explicit
reconciliation. Cancellation is a durable host-side flag: the runner polls it,
terminates the isolated process, and stores a cancelled result. The plugin never
receives the polling endpoint or control-plane credentials. A platform
administrator can close a post-deadline ambiguous claim as outcome unknown, but
cannot clear or replay it. The process-local adapter implements the same state
machine only for no-impact conformance and is not restart durable. Network,
secret, and action declarations grant nothing by themselves. Read-only network
and credential declarations are enabled only through the
[read mediation boundary](../specifications/plugin-mediation-contract.md).
Action declarations are enabled only for the
[proposal-only mediation boundary](../specifications/plugin-action-mediation-contract.md):
the plugin may place a proposal in the normal approval queue but never receives
approval, execution, reconciliation, or credential authority. Direct Docker
socket access belongs only to a dedicated runner deployment, never the API pod.

The trusted relay is released as its own multi-platform OCI layout alongside the
control plane. Its artifact entry, platform digests, SPDX SBOM, and SLSA
provenance are independently verified by the release manifest. A production
runner must publish and pin that verified bridge digest; it must not rebuild the
relay from a mutable working tree during plugin invocation.

The Kubernetes observer container accepts one invocation over stdin. Offline
tests can normalize a bundled provider fixture; the signed-runner conformance
path instead reads the fixture through the real invocation-local socket while
the container remains on Docker's `none` network. Its separate live development
transport still requires explicit kubeconfig and context. A separately signed
action-provider profile uses the same image and SDK to send one proposal request
through the trusted relay; conformance verifies the real governed action store
contains only a pending proposal, never an approval or execution result.
