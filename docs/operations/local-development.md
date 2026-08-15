# Local development

**Status:** Foundation

## Requirements

- Python 3.11 or newer
- Helm 3 or newer
- Optional: Docker Desktop for PostgreSQL integration tests and the durable local stack
- Optional: Docker Desktop, kind, and `kubectl` for live Kubernetes collection tests

Install the pinned verification-only Python dependencies:

```bash
python3 -m pip install --requirement requirements/verify.txt
```

The domain, application, surfaces, and SDK boundaries remain vendor-independent. The verification environment pins `jsonschema`, its format checkers, and Psycopg so validation and integration behavior cannot silently change with a developer's global environment.

## Verify

```bash
make verify
```

This validates JSON documents and internal links, enforces Python package directions, checks every contract schema and example with the pinned Draft 2020-12 validator, runs unit tests, lints the chart, and renders Kubernetes templates.

PostgreSQL integration tests skip when no database URL is supplied. With Docker Desktop running, execute the explicit durable-store gate:

```bash
make test-postgres
```

This starts an ephemeral PostgreSQL 18.4 container bound to `127.0.0.1:55432`, runs only the PostgreSQL integration suite, and removes its container and volume on exit.

Run the separate end-to-end recovery measurement with:

```bash
make test-backup-restore
```

It seeds the durable reference workflow, backs up the complete platform schema, restores into a fresh database, verifies every table and sequence plus projection consistency, prints measured local RPO/RTO evidence, and removes its isolated Compose project and volume. See the [backup and restore procedure](postgresql-backup-restore.md) for the measurement semantics and production gaps.

Keep deterministic contract, SDK, kernel, and observer-conformance tests in the normal `make verify` gate. Docker Desktop supplies external dependencies for integration tests; it is not required to validate pure normalization behavior. This split keeps feedback fast while still exercising PostgreSQL against the real engine.

The observer has an explicit `kubectl` development transport. It requires a named context and kubeconfig path, lists each resource API path independently, and normalizes the same public contract as the offline fixture. A complete result includes an aggregate checkpoint plus opaque per-path provider cursors. A later reconciliation request supplies that committed state in `spec.resume`; the observer runs bounded watches and then relists the full scope. Kubernetes `410 Gone` uses the same relist path, so an expired stream can never be mistaken for deletion.

Create the isolated cluster and run the live test:

```bash
kind create cluster --name iip-dev --wait 120s
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-live
```

The kind cluster runs as containers inside Docker Desktop. Docker Desktop's separate built-in Kubernetes feature is not required for this workflow and should normally remain disabled to avoid an unnecessary second context and control plane.

The target seeds `deploy/kubernetes/dev/seed-incident.yaml`, whose intentionally nonexistent image produces a real `ErrImagePull`/`ImagePullBackOff`. It verifies that the observer returns a complete canonical graph and an unhealthy Pod with only the safe waiting reason. It then deletes only the harmless reconciliation-probe ConfigMap, resumes from every committed per-path cursor, relists the complete scope, and requires one host-generated tombstone. This local transport does not replace the future credential broker or isolated plugin runner.

## Run the reference API

Create a high-entropy local Bearer token and configure only its SHA-256 verifier. Keep the token in the current shell; do not commit it, paste it into documentation, or place it in request payloads.

```bash
export IIP_DEV_BEARER_TOKEN="$(openssl rand -hex 32)"
IIP_DEV_TOKEN_DIGEST="$(printf '%s' "$IIP_DEV_BEARER_TOKEN" | shasum -a 256 | awk '{print $1}')"
export IIP_AUTH_IDENTITIES_JSON="{\"identities\":[{\"tokenSha256\":\"sha256:${IIP_DEV_TOKEN_DIGEST}\",\"actorId\":\"local-developer\",\"tenantId\":\"local\",\"roles\":[\"developer\"]}]}"
```

The verifier configuration is not a raw credential, but it must still be protected because weak tokens could be guessed offline. The generated token has 256 bits of entropy. The API fails closed at startup when authentication configuration is absent or malformed.

```bash
make run
```

The local surface stores resources in memory. Its hashed opaque-token authenticator is a local reference boundary, not a production identity provider.

```bash
curl -X POST http://localhost:8080/v1/resources \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  --data @contracts/examples/resource.json

curl -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  http://localhost:8080/v1/resources
```

## Run the durable Docker profile

The Compose profile requires a local-only password supplied at runtime and never committed:

```bash
export IIP_POSTGRES_PASSWORD="$(openssl rand -hex 24)"
docker compose -f deploy/docker-compose.yml up --build --detach
docker compose -f deploy/docker-compose.yml ps
```

This is the long-running development stack visible in Docker Desktop: the API plus PostgreSQL and a named database volume. It differs from `make test-postgres`, whose test container and volume are always removed on exit. Stop the development stack with `docker compose -f deploy/docker-compose.yml down`; add `--volumes` only when you intentionally want to delete its local database.

The API migrates the local Compose database on startup. Automatic migration is disabled by default in Helm and should be a separately controlled deployment step outside local development.

### Verify or rebuild resource projections

The PostgreSQL maintenance command verifies one explicit tenant against immutable accepted observation history. It is read-only unless `--apply` is present and prints only stable counts and canonical digests.

```bash
export IIP_DATABASE_URL='postgresql://postgres:<local-password>@127.0.0.1:5432/iip'
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator
```

Review `driftDetected`, `beforeDigest`, and `expectedDigest`. Apply the atomic replacement only when recovery is intended:

```bash
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator --apply
```

This command does not alter immutable observations, events, outbox delivery state, checkpoints, or reconciliation membership. It is local operator tooling and does not create a cross-tenant HTTP administration path.

## Helm

```bash
helm lint deploy/helm/infra-intelligence
helm template iip deploy/helm/infra-intelligence --namespace iip-system
```

The default image reference is a placeholder until an image pipeline exists. Do not install the chart into a production cluster.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_HTTP_HOST` | `0.0.0.0` | Reference API bind address |
| `IIP_HTTP_PORT` | `8080` | Reference API port |
| `IIP_AUTH_IDENTITIES_JSON` | required by API startup | Local Bearer-token verifier identities; supply through protected runtime configuration |
| `IIP_DATABASE_URL` | unset | Select the PostgreSQL profile when set |
| `IIP_DATABASE_AUTO_MIGRATE` | `false` | Apply packaged migrations at startup; local Compose only |
| `IIP_TEST_DATABASE_URL` | unset | Enable PostgreSQL integration tests against an explicit test database |
| `IIP_KUBECONFIG` | required by live test | Explicit kubeconfig path; the observer never chooses an implicit current context |
| `IIP_KUBE_CONTEXT` | `kind-iip-dev` | Explicit local context used only by `test-kubernetes-live` |

Future secrets must be logical references resolved by the deployment/runtime, never committed environment files.
