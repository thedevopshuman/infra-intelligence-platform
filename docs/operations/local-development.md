# Local development

**Status:** Foundation

## Requirements

- Python 3.11 or newer
- Helm 3 or newer
- Optional: Docker Desktop for PostgreSQL integration tests and the durable local stack
- Optional: Kubernetes cluster and `kubectl` for deployment experiments

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

Keep deterministic contract, SDK, kernel, and observer-conformance tests in the normal `make verify` gate. Docker Desktop supplies external dependencies for integration tests; it is not required to validate pure normalization behavior. This split keeps feedback fast while still exercising PostgreSQL against the real engine.

Docker Desktop Kubernetes can become the local live-collector integration environment after the Kubernetes API client and host credential broker are implemented. The current observer intentionally runs from an offline list fixture, so enabling a cluster does not expand its authority or turn the fixture CLI into a production plugin runtime.

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

Future secrets must be logical references resolved by the deployment/runtime, never committed environment files.
