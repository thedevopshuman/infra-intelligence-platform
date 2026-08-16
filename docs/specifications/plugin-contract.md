# Plugin contract

**Status:** v1alpha1 manifest and signed OCI execution profile
**Machine contract:** `contracts/schemas/plugin-manifest.schema.json`

Plugins extend the platform without linking vendor code into the kernel. A plugin may contribute resource observers, event sources, evidence providers, actions, or user-facing surfaces.

## Package requirements

A publishable plugin contains:

- a schema-valid manifest;
- implementation artifact and immutable digest;
- signature and publisher identity;
- configuration schema with secret references, never values;
- capability-specific input/output schemas;
- compatibility range for the plugin protocol;
- health and readiness behavior;
- contract tests and least-privilege deployment guidance.

## Manifest permissions

Permissions are declarative upper bounds:

- `network`: approved destinations and ports;
- `secrets`: logical names the broker may resolve;
- `resources`: read/write scopes;
- `actions`: named mutation capabilities.

Install policy and request policy may narrow these. Runtime discovery cannot expand them.

`spec.interfaces` is an additive `v1alpha1` declaration that maps an advertised capability and method to public input/output schema identifiers. Every declared interface capability must also appear in `spec.capabilities`; a host rejects undeclared methods. The Kubernetes example declares `resource-observer.collect` using the [resource collection request/result contracts](resource-collection-contract.md). The first runtime framing is one `PluginInvocation` JSON document on stdin and one method-output JSON document on stdout.

`spec.artifact` is an additive descriptor for executable releases. Legacy
handshake-only manifests may omit it, but the signed runner requires an immutable
OCI reference, matching SHA-256 digest, Ed25519 key ID, and signature. The
signature covers canonical JSON with this exact shape:

```json
{
  "apiVersion": "iip.plugin-signature/v1",
  "pluginId": "kubernetes-observer",
  "pluginVersion": "0.3.0",
  "protocolVersion": "1.0",
  "artifact": {
    "type": "oci-image",
    "reference": "repository/image@sha256:...",
    "digest": "sha256:..."
  }
}
```

Publisher keys are explicit installation trust roots. A manifest signature does
not grant capabilities; it only proves that the trusted publisher bound this
plugin identity and version to this exact artifact.

## Handshake

The `PluginSession` handshake exchanges protocol version, plugin identity and
manifest digest, a narrowed capability set, cancellation support, and request,
time, and output limits. The host provides request-scoped tenant/actor context
and verifies capability-token material without copying that material into the
invocation or plugin environment. Unknown methods or incompatible schema
versions fail closed.

## Execution

Default execution is out-of-process through `stdio`, authenticated HTTP/gRPC, or
a WASM sandbox. The first accepted profile is the [signed Docker runner](../architecture/plugin-runtime.md):
pre-pulled image, immutable digest, no network, no mounts or environment secrets,
read-only root, non-root UID, no Linux capabilities, `no-new-privileges`, and
bounded CPU, memory, PIDs, files, temporary storage, deadline, input, stdout, and
stderr. Every call has a request ID and structured error code. Raw exceptions
and credentials never cross the boundary.

## Compatibility

Plugin versions are immutable. Patch releases fix behavior without contract change; minor releases add backward-compatible capabilities; major releases may break plugin-specific methods. Plugin protocol changes have their own compatibility range independent of plugin semantic version.
