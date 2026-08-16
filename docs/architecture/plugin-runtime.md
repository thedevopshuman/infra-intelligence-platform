# Signed plugin runtime

**Status:** Accepted first isolation profile

Plugins remain untrusted even when their publisher signature is valid. The
platform verifies trust and authority in this order:

1. resolve an explicitly installed publisher/key trust root;
2. verify the Ed25519 signature over plugin identity, protocol, immutable image
   reference, and digest;
3. bind the manifest digest, actor, tenant, capability token digest, declared
   method, deadline, and limits to an active `PluginSession`;
4. atomically claim the request ID within the runner ledger;
5. start the already-present image by immutable digest with Docker networking
   disabled and no host mounts, inherited plugin environment, or credentials;
6. enforce read-only root, non-root UID, dropped capabilities,
   `no-new-privileges`, CPU, memory/swap, PID, file-descriptor, temporary storage,
   wall-time, input, stdout, and stderr limits;
7. wrap one bounded JSON object in a host-created `PluginInvocationResult` and
   pass it to the owning contract validator before application use.

The Docker client uses `--pull=never`. Artifact acquisition and signature review
are separate installation actions; invocation cannot contact a registry or
substitute another tag. Container exit, timeout, oversized output, malformed
JSON, signature failure, scope mismatch, and unknown methods become stable error
codes without stderr or provider text.

The current process-local ledger prevents duplicate IDs and enforces request
counts while one runner process is alive. It is sufficient for the no-impact
conformance profile. Network, secret, and action declarations are denied. Before
any side-effecting or externally connected plugin is enabled, the platform must
add a durable request claim/result store plus a mediated network/credential
proxy whose grants are narrower than the manifest and session. Direct Docker
socket access belongs only to a dedicated runner deployment, never the API pod.

The Kubernetes observer container is an offline conformance artifact: it accepts
one invocation over stdin and normalizes a bundled provider fixture. Its separate
live development transport still requires explicit kubeconfig and context and
does not run under this no-network production profile.
