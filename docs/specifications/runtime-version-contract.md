# Runtime version contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/runtime-version-report.schema.json`

`RuntimeVersionReport` identifies the application and public contract versions served by one control-plane process, the newest packaged PostgreSQL migration it requires, and any verifiable build or deployment identity supplied to that process. It gives customers and support engineers a shared answer to “what is actually running?” without relying on mutable image tags or browser assets.

`build.mode` is `release` only when the image contains an exact 40–64 character lowercase hexadecimal source revision. A development build does not claim a revision. Helm injects its own chart version and the exact configured image digest; those fields are omitted when the process was not deployed with that evidence. The service never derives an image digest from a tag and never reports a placeholder as verified identity.

## API and authority

`GET /v1/system/version` requires the normal control-plane Bearer credential. The server derives `metadata.tenantId` from that credential; callers cannot select it. Every authenticated tenant may inspect the same non-secret runtime facts so customer users can attach the report to support and upgrade workflows.

The response contains no credential, endpoint, hostname, pod name, provider configuration, database connection detail, customer data, or package inventory. It reports the required packaged migration rather than querying or exposing database internals. `/readyz` separately proves that the required migration is applied before the process serves traffic.

The report describes the API process answering the request. In a multi-replica deployment, every process should have the same immutable image digest and chart version; this endpoint does not aggregate replicas or worker identities.
