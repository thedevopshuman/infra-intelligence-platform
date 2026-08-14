# Plugin contract

**Status:** v1alpha1 manifest; handshake protocol pending  
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

`spec.interfaces` is an additive `v1alpha1` declaration that maps an advertised capability and method to public input/output schema identifiers. Every declared interface capability must also appear in `spec.capabilities`; a host may reject undeclared methods. The Kubernetes example declares `resource-observer.collect` using the [resource collection request/result contracts](resource-collection-contract.md). Transport negotiation and runtime method framing remain part of the pending handshake.

## Handshake

The planned protocol handshake exchanges protocol version, plugin identity and digest, capabilities, method schemas, health, and cancellation support. The host provides request-scoped tenant/actor context, deadlines, trace context, and capability tokens. Unknown methods or incompatible schema versions fail closed.

## Execution

Default execution is out-of-process through `stdio`, authenticated HTTP/gRPC, or a WASM sandbox. The host enforces CPU, memory, output-size, concurrency, deadline, network, and secret policies. Every call has a request ID and structured error code. Raw exceptions and credentials never cross the boundary.

## Compatibility

Plugin versions are immutable. Patch releases fix behavior without contract change; minor releases add backward-compatible capabilities; major releases may break plugin-specific methods. Plugin protocol changes have their own compatibility range independent of plugin semantic version.
