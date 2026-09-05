# AWS Bedrock public price import

**Status:** Executable provider-source adapter

Use this workflow to acquire one official AWS Price List Bulk API snapshot,
map reviewed Bedrock dimensions into an IIP catalog, retain minimized import
evidence, and then run the independent production-catalog qualification gate.
The command requires no AWS credential and never contacts a model endpoint.

## 1. Prepare and review the policy

Copy
`contracts/examples/aws-bedrock-price-catalog-import-policy.json` to a
protected configuration repository. Replace the synthetic entries with exact
values inspected in the selected `AmazonBedrock` offer. Keep entries sorted by
`id`, choose an immutable source version URL for promotion, and increment both
the policy and output catalog versions whenever any mapping changes.

The checked example and `tests/fixtures/aws-bedrock-price-list.json` are
synthetic contract data. They are not AWS prices and must not be installed as
customer configuration.

After editing, calculate the expected content-derived policy ID:

```bash
PYTHONPATH=src .venv/bin/python scripts/import_aws_bedrock_price_catalog.py \
  policy-id --policy /protected/aws-bedrock-import-policy.json
```

Set `metadata.id` to the printed value and run the command again. A policy is
ready only when the reported ID equals the stored ID and the subsequent import
accepts its complete closed shape.

## 2. Fetch and retain the exact source

Run from a controlled host with verified system CA roots. Store the raw source,
generated catalog, and minimized report in separate protected paths:

```bash
PYTHONPATH=src .venv/bin/python scripts/import_aws_bedrock_price_catalog.py \
  fetch \
  --policy /protected/aws-bedrock-import-policy.json \
  --source-output /protected/snapshots/amazon-bedrock-index.json \
  --catalog-output /protected/catalogs/aws-bedrock.json \
  --report-output /protected/evidence/aws-bedrock-import-report.json
```

The fetch client accepts only the policy URL, uses direct verified HTTPS,
disables environment proxies, rejects redirects, bounds the response by the
policy, and retains the exact response bytes before promotion. If any expected
SKU, attribute, term, dimension, unit, effective time, or decimal conversion
does not match, no catalog/report pair is produced.

Do not overwrite the prior snapshot or evidence. Use versioned paths or an
append-only artifact store and restrict read access to pricing/release
operators. The minimized report may enter the release evidence channel; the
policy, raw source, and catalog remain protected configuration artifacts.

## 3. Reproduce the import

Before review or promotion, exact-verify all four artifacts:

```bash
PYTHONPATH=src .venv/bin/python scripts/import_aws_bedrock_price_catalog.py \
  verify \
  --source /protected/snapshots/amazon-bedrock-index.json \
  --policy /protected/aws-bedrock-import-policy.json \
  --catalog /protected/catalogs/aws-bedrock.json \
  --report /protected/evidence/aws-bedrock-import-report.json
```

`generate` can reconstruct the outputs from the retained source during audit
or recovery. Supply the report's original retrieval timestamp; a different
timestamp deliberately produces a different catalog and report identity.

```bash
PYTHONPATH=src .venv/bin/python scripts/import_aws_bedrock_price_catalog.py \
  generate \
  --source /protected/snapshots/amazon-bedrock-index.json \
  --policy /protected/aws-bedrock-import-policy.json \
  --retrieved-at 2026-09-05T10:00:00Z \
  --catalog-output /tmp/reproduced-catalog.json \
  --report-output /tmp/reproduced-report.json
```

## 4. Qualify and promote separately

Import success is necessary but not sufficient. Qualify the generated catalog
against the tenant's protected scope/freshness policy:

```bash
IIP_AI_PRICE_CATALOG_FILE=/protected/catalogs/aws-bedrock.json \
IIP_AI_PRICE_QUALIFICATION_POLICY=/protected/aws-price-qualification-policy.json \
IIP_AI_PRICE_QUALIFICATION_REPORT=/protected/evidence/aws-price-qualification.json \
  make qualify-ai-price-catalog PYTHON=.venv/bin/python
```

Require an independent reviewer to verify the import report, inspect the
mapping and source snapshot, record organizational approval, and verify the
production qualification report before rotating the worker Secret described
in [AI cost engine operations](ai-cost-engine.md). The running worker does not
fetch AWS prices; it repeatedly verifies the exact qualification evidence and
stops before using a stale or altered catalog.

## Limitations and required controls

- This profile supports the public `AmazonBedrock` bulk offer only. It does not
  import Marketplace, provisioned-throughput, discount, commitment, tax,
  credit, private-offer, or CUR/invoice facts unless an exact supported mapping
  is explicitly added later.
- Public AWS pricing and a customer's bill can differ. Continue labeling IIP
  output `calculated-estimate`, and reconcile billing through a future separate
  variance contract rather than changing usage or cost history.
- TLS acquisition authenticates the configured AWS origin at fetch time; the
  retained digest proves bytes thereafter. Preserve command audit and artifact
  provenance according to customer change-control policy.
- A provider description change never authorizes a guessed mapping. Update the
  policy, produce new evidence, and requalify.
- The importer is an operator tool outside the collection/runtime kernel. It
  has no telemetry-channel credential, tenant data access, model SDK, prompt,
  response, or inference-path authority.

## Provider references

- [AWS Price List Bulk API file retrieval](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-the-aws-price-list-bulk-api-fetching-price-list-files-manually.html)
- [AWS Price List offer-file format](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/bulk-api-reading-price-list-files.html)
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)
