# Repository structure and dependency rules

**Status:** Accepted foundation  
**Date:** 2026-08-14

The code begins as a modular monolith with ports and adapters. Dependency direction is the primary guard against a future distributed monolith.

```mermaid
flowchart TD
    Surfaces["surfaces: HTTP, CLI, workers"] --> Bootstrap["bootstrap: composition root"]
    Surfaces --> Application["application: use cases and ports"]
    Bootstrap --> Adapters["adapters and plugins"]
    Bootstrap --> Application
    Adapters --> Application
    Adapters --> Domain["domain: identity and rules"]
    Application --> Domain
    SDKs["public SDKs"] --> Contracts["versioned public contracts"]
    Plugins["external plugins"] --> Contracts
```

## Rules

| Layer | Owns | May depend on | Must not depend on |
| --- | --- | --- | --- |
| `domain` | Identity, immutable concepts, pure rules | Python standard library | Application, adapters, surfaces, vendor SDKs |
| `application` | Use cases and provider-neutral ports | Domain | Concrete adapters, surfaces, vendor SDKs |
| `adapters` | Persistence, buses, models, policies, providers | Application, domain | Surfaces |
| `surfaces` | HTTP/CLI/message protocols and presentation | Application; bootstrap for composition | Vendor logic and domain policy branches |
| `bootstrap` | Concrete dependency graph and runtime profiles | All server layers | Business rules |
| `sdks` | Public client types, transport, errors | Public contracts | Server internals |
| `plugins` | Separately versioned extension behavior | Plugin protocol and public contracts | Server internals or ambient process state |

The reference code under `src/iip` proves these directions. Future languages or services keep the same ownership even when process boundaries change. `src/iip/surfaces/worker.py` is the deployable investigation-worker surface: its claim/retry/cancellation sequence stays in `application`, PostgreSQL lease mechanics stay in `adapters`, and concrete construction stays in `bootstrap.py`.

The Kubernetes observer under `plugins/examples/` is the first executable proof of the plugin boundary. It imports only the public Python SDK, consumes versioned resource collection and invocation contracts, supports an offline stdio container plus an explicit bounded `kubectl` per-type list/watch development transport with full-reconciliation recovery, and passes the signed no-network runner gate. The runner is a concrete adapter; it never moves container or trust behavior into the plugin, application, or domain packages. The repository validator applies the same no-server-internals rule to all Python plugin packages.

## Placement decisions

- A provider-specific API call belongs in `adapters/<provider>` or a plugin.
- A capability selected by an agent is a tool contract; the implementation delegates to a provider port.
- A use-case sequence belongs in `application`, not an HTTP handler or queue consumer.
- Shared data crossing a process boundary belongs in `contracts`, not a copied internal class.
- A derived read model belongs in an adapter and is rebuildable from domain records.
- A component that needs both plugin discovery and a use case is wired in `bootstrap`.

## Planned top-level expansion

Add directories only with an executable slice:

```text
apps/                 independently deployable surfaces/workers
packages/             shared implementation packages after language expansion
evaluations/          incident scenarios, expected evidence, scoring
deploy/operators/     collectors or operators deployed into target environments
```

Do not create empty service forests. The roadmap defines when a process boundary becomes necessary.
