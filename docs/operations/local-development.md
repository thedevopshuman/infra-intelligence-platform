# Local development

**Status:** Foundation

## Requirements

- Python 3.9 or newer
- Helm 3 or newer
- Optional: Kubernetes cluster and `kubectl` for deployment experiments

Install the pinned verification-only Python dependencies:

```bash
python3 -m pip install --requirement requirements/verify.txt
```

Application and SDK runtime packages remain dependency-free. The verification environment pins `jsonschema` and its format checkers separately so contract validation cannot silently change with a developer's global environment.

## Verify

```bash
make verify
```

This validates JSON documents and internal links, enforces Python package directions, checks every contract schema and example with the pinned Draft 2020-12 validator, runs unit tests, lints the chart, and renders Kubernetes templates.

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

Future secrets must be logical references resolved by the deployment/runtime, never committed environment files.
