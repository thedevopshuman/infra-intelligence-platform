# Installation options

**Status:** Release-planning reference; public v1 installation remains open

IIP has several deployment assets, but they do not have the same purpose or
release status. The accepted release direction is:

1. make the persistent single-host Compose installation the primary, usable
   public path; then
2. add a separate full-stack Kubernetes installation profile delivered through
   Helm.

The second path must not relabel or weaken the existing production-oriented
Helm chart. That chart deliberately integrates with customer-owned services.
The future bundled profile needs its own documented support boundary, values,
tests, upgrade and recovery behavior before it can be offered as an
installation option.

## Current and planned paths

| Path | Current availability | What it runs | Operator-provided dependencies | Login | Persistence and recovery | Important limits |
| --- | --- | --- | --- | --- | --- | --- |
| Persistent single-host Compose | Implemented on development `main`; candidate source kit packages the non-Git path, but kit/images are not publicly released or fresh-host qualified | IIP API, migrator, worker, isolated AI usage receiver, PostgreSQL, OpenTelemetry Collector, Prometheus, and Grafana | A trusted local Docker Engine or Docker Desktop with Compose v2, pinned host Python dependencies, reviewed channel/pricing/qualification/attribution inputs, and the instrumented application that calls its provider | Generated local Bearer token for the IIP console/API; separate generated Grafana administrator password | Named volumes persist database, Collector queue, Prometheus, and Grafana data; encrypted stopped-stack recovery and stopped-stack trust rotation are implemented | One trusted host, one tenant and Bedrock channel, loopback interfaces, no external identity/ingress or HA, no published runtime artifact, no unattended certificate renewal, and incomplete long-term ledger/outbox retention |
| Bundled Kubernetes/full-stack Helm profile | Planned second installation path; not implemented or released | Intended to install the IIP runtime and the dependencies needed for a self-contained usable stack | Not yet defined; the profile must state storage class, cluster capacity, DNS/ingress, certificate and backup responsibilities rather than silently inheriting production assumptions | Not yet selected for this profile; it must use an existing documented auth mode and must not present a local development token as an internet-facing production identity | Not yet defined; durable volumes, backup/restore, upgrade and certificate lifecycle need executable qualification | No chart, values contract, support matrix, install command, or qualification evidence exists yet |
| Existing Helm chart | Executable bring-your-own-dependencies reference path; not a complete public-v1 installation | IIP API, optional worker and isolated receivers, schema migration job, optional backup CronJob, ClusterIP/optional Ingress, NetworkPolicies, and optional Prometheus rules | Immutable IIP image, PostgreSQL and CA, credential-bearing Secrets, identity provider for production console login, ingress controller and TLS material when exposed, backup storage, Collector/metrics backend, Prometheus/Alertmanager routing, registry and any enabled policy or credential services | OIDC access-token verification and optional browser Authorization Code with S256 PKCE for production; local hashed identities remain a development mode | Authoritative data is in the customer PostgreSQL service; backup storage, HA, fencing, PITR and disaster recovery remain customer-owned and separately qualified | Default image repository is a placeholder; the chart does not install PostgreSQL, Collector, Prometheus, Grafana, Alertmanager, an IdP, ingress controller, certificates, Secret controller or registry |
| `learning-v0.84.0` source prerelease | Published source-only learning milestone | Disposable synthetic learning topology | Source checkout and local Docker dependencies | Known local learning credential; not an identity lifecycle | Disposable fixture data | Not a durable installation, provider qualification, signed runtime distribution or production release |

Follow the [persistent community installation](community-installation.md) only
for the current development-main preview. Its commands build or select an
image already present on the host. The [installation source kit](community-installation-kit.md)
packages these tools but is not yet published or qualified as a public-v1
installation. Follow the [Helm deployment runbook](helm-deployment.md) only when its
external prerequisites are intentionally supplied. Successful `helm lint`,
rendering or preflight does not turn that chart into the planned bundled
Kubernetes profile.

The [learning release](../releases/learning-v0.84.0.md) remains useful for a
disposable guided session, but it is not the starting point for durable public
installation claims.

## Authentication modes

Authentication is selected through deployment configuration, not a console
toggle:

- **Local hashed token** validates an operator-provisioned Bearer token. It is
  appropriate for the loopback single-host profile and local development; it
  has no issuer, federation, refresh, revocation or account-management
  lifecycle.
- **Access token** validates a JWT issued by an external OIDC provider. The
  operator obtains and supplies the token; IIP does not provide a browser
  sign-in flow in this mode.
- **OIDC PKCE** adds browser Authorization Code with S256 PKCE to the same
  access-token verifier. The customer owns IdP registration, claims, MFA,
  consent, logout, revocation, key/certificate availability and CORS.

IIP does not provide a username/password database, social login, hosted IdP or
token-issuance service. OTLP mTLS plus channel Bearer authentication is
workload intake identity, not a user login. See the
[authentication boundary](../architecture/authentication-boundary.md),
[console authentication contract](../specifications/console-authentication-contract.md),
and [OIDC operating guide](oidc-identity.md).

## Integrations, telemetry and plugins

An integration being implemented does not mean it is automatically enabled or
qualified for every installation.

| Capability | Boundary | How it is enabled | Authority and current availability |
| --- | --- | --- | --- |
| OTLP AI usage, metrics and logs intake | Built-in protocol receivers, not plugins | Protected channel and workload-identity configuration, an isolated receiver, and customer/application exporter configuration | Disabled by default outside the community AI profile. Production requires verified TLS, exact mTLS identity-to-channel binding, a separate channel token, durable PostgreSQL and a customer-controlled Collector queue. See [AI usage intake](ai-usage-receiver.md), [metrics intake](otlp-metrics-receiver.md), and [log evidence](log-evidence.md). |
| IIP platform telemetry export | Built-in OpenTelemetry adapter, not a plugin | Deployment configuration for the selected OTLP endpoint and privacy mode | Disabled by default; the destination Collector/backend and its credentials, queue and lifecycle remain operator-owned. See [OpenTelemetry export](opentelemetry-export.md). |
| Bedrock usage capture | Separately installed instrumentation beside the customer's provider client | Enable the qualified botocore instrumentation scope and export metadata through the customer's Collector | IIP receives no AWS credentials and is not an inference proxy. Prompt, message and body capture stay disabled. Exact live model/region qualification remains release evidence. See [Bedrock instrumentation qualification](bedrock-instrumentation-qualification.md). |
| Prometheus, Loki and OpenSearch evidence | Built-in historical-query adapters | Select the backend and provide a protected integration registry, allowlisted query catalog, verified transport and narrowly scoped credentials where required | Default is honest no-data; these are not installed or activated from the UI. See [Prometheus evidence](prometheus-evidence.md) and [log evidence](log-evidence.md). |
| Kubernetes Events and governed actions | Separate built-in read and action adapters | Select the backend/executor, exact clusters/namespaces/types, verified Kubernetes API identity, and explicit egress; live actions also require policy, idempotency, audit and approval | Event reads are GET-only. Actions default to dry-run and require a second live-execution switch; no console toggle grants cluster authority. See [Kubernetes Event evidence](kubernetes-event-evidence.md) and [Kubernetes actions](kubernetes-actions.md). |
| File and GitHub context | Built-in context adapters | Select the backend and protected immutable-file/repository configuration; production GitHub reads use request-scoped brokered authority | Default is honest no-data. Static tokens are a disposable-development fallback, not the production credential design. See [context operations](context-evidence.md). |
| Kubernetes observer | The repository's executable plugin example | Run its offline fixture/conformance flow or the explicitly scoped local Kind developer path | It is not a published plugin product: its manifest still uses a local-development publisher and placeholder image. There is no marketplace, install/configure API, or UI enable switch. See the [example](../../plugins/examples/kubernetes-observer/README.md) and [plugin runner](plugin-runner.md). |

Provider adapters are selected through protected deployment configuration and
usually a process restart. Plugins consume public contracts through the
sandboxed runtime and receive no ambient credentials or unrestricted network
authority. OpenTelemetry is the portable collection/export protocol; it is not
a plugin system. The console can operate existing plugin invocations, but it
does not install, sign, trust or configure plugins.

## What must be true before either public path is called v1

- Installation uses versioned published artifacts rather than relying on a
  source build, and a fresh supported host or cluster reproduces it.
- The supported feature, backend, host/cluster and compatibility matrix is
  explicit; unqualified adapters and plugin hosting are marked experimental.
- A real Bedrock invocation reaches the durable ledger, qualified pricing and
  attribution, rolling metrics and Grafana without prompt/response capture.
- The selected path passes exact-artifact identity, transport, tenant,
  upgrade, backup/restore, certificate, capacity, monitoring and retention
  gates appropriate to its stated scope.
- Supported public contracts have a compatibility and migration promise;
  remaining `v1alpha1` surfaces are clearly experimental.

The [public v1 release plan](../roadmap/public-v1-release-plan.md) tracks these
gates. Neither local health, a synthetic test, a rendered chart nor the
existence of an adapter substitutes for release evidence.
