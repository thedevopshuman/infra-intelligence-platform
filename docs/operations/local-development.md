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

```bash
make run
```

The local surface trusts development-only headers and stores everything in memory. It is intentionally not production-authenticated or durable.

```bash
curl -X POST http://localhost:8080/v1/resources \
  -H 'content-type: application/json' \
  -H 'x-iip-tenant-id: local' \
  -H 'x-iip-actor-id: developer' \
  --data @contracts/examples/resource.json

curl -H 'x-iip-tenant-id: local' http://localhost:8080/v1/resources
```

## Run the durable Docker profile

The Compose profile requires a local-only password supplied at runtime and never committed:

```bash
export IIP_POSTGRES_PASSWORD="$(openssl rand -hex 24)"
docker compose -f deploy/docker-compose.yml up --build
```

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
| `IIP_DATABASE_URL` | unset | Select the PostgreSQL profile when set |
| `IIP_DATABASE_AUTO_MIGRATE` | `false` | Apply packaged migrations at startup; local Compose only |
| `IIP_TEST_DATABASE_URL` | unset | Enable PostgreSQL integration tests against an explicit test database |

Future secrets must be logical references resolved by the deployment/runtime, never committed environment files.
