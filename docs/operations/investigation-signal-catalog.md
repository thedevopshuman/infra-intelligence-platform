# Protected investigation signal catalog

The signal catalog turns reviewed operational knowledge into automatic, reproducible evidence planning. It is optional: without a profile, investigations behave exactly as before and use only candidates supplied in the request.

Start from `contracts/examples/investigation-signal-catalog.json`. Create at most one profile for each exact tenant. Replace the example logical integration IDs, metric/service names, runbook references, condition keys, and limits with values already allowed by the corresponding Kubernetes Event, context, resource-history, Prometheus-compatible, and Loki integration catalogs.

The profile is not a credential store. Do not place tokens, endpoints, provider query languages, resource IDs, time ranges, alert text, or secrets in it. Each referenced backend still resolves its own protected integration configuration and request-scoped credential lease after policy approval.

## Local Docker

`make dev-up` installs a small `local` profile that asks the internal resource-history provider about image and scale changes for an unavailable rollout. The console authorizes all supported read-only signal classes within its explicit investigation budget; only configured profile candidates are added. Completed reports show each plan step as `Reviewed catalog` or `Request`.

To replace the local profile, set `IIP_INVESTIGATION_SIGNAL_CATALOG_JSON` in `.iip/local.env` to one compact JSON document and rebuild the API and worker. The local lifecycle preserves an existing value.

## Helm

Store the complete catalog document in an existing Secret, then reference only its name and key in reviewed values:

```bash
kubectl --namespace iip-system create secret generic iip-signal-catalog \
  --from-file=investigation-signal-catalog-json=investigation-signal-catalog.json
```

```yaml
investigationSignalCatalog:
  existingSecret: iip-signal-catalog
  secretKey: investigation-signal-catalog-json
```

The chart projects the same protected document into the API and workflow worker. The API freezes generated candidates before queue persistence. Workers execute the frozen snapshot and verify its digest; they do not silently re-resolve a newer profile for already queued work. Restart the deployments after rotating the referenced Secret so new requests use the new profile version.

## Safe rollout

1. Validate the document with `make verify` or the public JSON Schema.
2. Increase the profile semantic version whenever candidate behavior changes.
3. Deploy to one evaluation tenant and inspect `signalPlan`, evidence usage, no-data rates, latency, and backend policy decisions.
4. Confirm request `evidenceTypes`, `allowedTools`, and budgets defer signals as expected.
5. Promote the exact profile digest through the normal configuration review path.

A malformed profile fails process composition. A caller-supplied or modified `catalogSnapshot` is rejected. Profile changes affect only newly prepared investigations; the accepted request and terminal report retain the exact profile and resolved-subset digests needed for support and replay analysis.
