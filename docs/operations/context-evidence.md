# Repository and runbook context operations

**Status:** Runnable local adapter

The default context backend returns honest no-data. The `files` backend reads only explicitly cataloged UTF-8 text files under a protected absolute root. Symlink resolution must remain under that root, parent traversal and absolute catalog paths are rejected, and only documented source/text extensions are accepted.

## Docker Desktop

The local Compose stack mounts `deploy/context` read-only at `/var/run/iip-context`. Enable the example catalog before `make local-up`:

```bash
export IIP_CONTEXT_BACKEND=files
export IIP_CONTEXT_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/context/integrations.example.json)"
make local-up
```

Ingest the canonical resource example, then update the request timestamps/deadline and submit `contracts/examples/context-evidence-request.json` to `POST /v1/evidence/context/queries`. The response is Evidence metadata; normalized artifact bytes remain internal.

## Helm

Set `contextEvidence.backend=files`, place the catalog JSON in a Secret key, set `contextEvidence.integrationsExistingSecret`, and mount the cataloged documents through `contextEvidence.documentsExistingConfigMap`. Every configured `root` must equal or contain the chart mount path. Large or frequently changing repositories should use a future remote adapter or immutable synchronized volume instead of a ConfigMap.

Do not put repository credentials, secrets, private keys, tokens, or sensitive configuration values in the catalog JSON or example documents. The adapter performs mandatory text redaction, but prevention and least-privilege source access remain primary controls.
