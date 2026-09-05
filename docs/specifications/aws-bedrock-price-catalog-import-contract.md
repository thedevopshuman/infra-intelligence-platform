# AWS Bedrock price-catalog import contracts

**Status:** v1alpha1

**Machine contracts:**

- `contracts/schemas/aws-bedrock-price-catalog-import-policy.schema.json`
- `contracts/schemas/ai-price-catalog-import-report.schema.json`
- `contracts/schemas/ai-price-catalog.schema.json`

These contracts turn one retained AWS Price List Bulk API snapshot into the
provider-neutral `AiPriceCatalog` consumed by the cost engine. Import is an
offline release/configuration operation. It is not part of OTLP collection,
request serving, or the model invocation path.

## `AwsBedrockPriceCatalogImportPolicy`

The protected policy declares the exact interpretation of one AWS
`AmazonBedrock` offer. Its ID is the first 32 hexadecimal characters of the
canonical SHA-256 digest over `tenantId`, policy `version`, and `spec`, prefixed
with `abp_`. Changing a mapping therefore requires a new ID and reviewed
version.

The source profile accepts only HTTPS documents on
`pricing.us-east-1.amazonaws.com` at the AWS Price List Bulk API
`/offers/v1.0/aws/AmazonBedrock/<version>/index.json` path. `current` is
accepted for acquisition; the generated catalog always records the immutable
version URL declared inside the accepted document. A versioned URL is still
preferred in a promoted policy. Query strings, user information, fragments, alternate ports,
redirects, and environment proxies are not accepted by the fetch adapter.

Each sorted entry binds one public catalog scope to five exact AWS rate
references. A reference includes:

- the exact SKU, offer-term code, and rate code;
- `1K tokens` or `1M tokens` as the expected billing unit; and
- the expected `feature`, `inferenceType`, `model`, `provider`, `regionCode`,
  and `usagetype` product attributes.

The importer does not parse price descriptions or infer public model IDs,
regions, tiers, routing, or purchase modes. Those mappings are protected human
decisions. All five canonical IIP meters are mandatory. A policy may
deliberately reference the same AWS price dimension for non-reasoning and
reasoning output when the reviewed provider price is identical.

## Generated `AiPriceCatalog`

The importer requires an `aws_v1` source envelope with offer code
`AmazonBedrock`. A versioned locator must equal the source document's declared
version; `current` remains valid only as an explicitly reviewed rolling input.
For every rate it exact-matches the product, attributes,
on-demand term, effective date, unbounded price dimension, unit, and USD value.
One mismatch fails the complete import; partial catalogs are never emitted.

Conversion never uses binary floating point. If the source price is `P` USD
per unit and the catalog scale is `S`, the price in subunits per million tokens
is:

```text
P × (1000 when the source unit is 1K tokens, otherwise 1) × 10^S
```

The result must already be an integer and fit the JSON safe-integer limit. The
importer neither rounds a provider rate nor interprets a missing rate as zero.
The generated catalog has provider `aws.bedrock`, source kind
`provider-published`, the raw source-byte SHA-256 digest, and content-derived
`apc_` identity.

## `AiPriceCatalogImportReport`

The report makes the transformation reproducible while keeping public price
details out of release evidence. It binds:

- source version, content digest, byte count, publication time, and retrieval time;
- policy ID, version, and document digest;
- catalog ID, version, document digest, currency, scale, and entry count; and
- aggregate rate-reference and unique-dimension counts.

It contains no source locator, SKU, term code, rate code, model, provider
product name, usage type, or numeric rate. Re-verification imports the retained
source again and exact-compares both the catalog and report.

An imported report proves deterministic transformation under one policy. It
does not prove that AWS will bill the customer at those rates, that private or
contract prices were included, that the organization approved promotion, or
that calculated cost reconciles to an invoice. The separate
`AiPriceCatalogQualificationReport` proves freshness, non-overlap, and required
scope coverage; the production worker requires that qualification before use.
