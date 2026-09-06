# Customer Bedrock qualification contracts

**Status:** v1alpha1 executable customer-environment qualification

The customer Bedrock qualification is an explicitly enabled, one-call proof
that one exact approved AWS Bedrock model, region, and operation work through
the pinned official Python botocore instrumentation, the separately installed
IIP usage adapter, and the shipped IIP metadata-only normalization boundary.
It adds no runtime inference proxy or product SDK.

The contracts are:

- `contracts/schemas/customer-bedrock-qualification-profile.schema.json`;
- `contracts/schemas/customer-bedrock-qualification-report.schema.json`; and
- the underlying
  `contracts/schemas/bedrock-instrumentation-compatibility-report.schema.json`.

## Protected profile

`CustomerBedrockQualificationProfile` selects one protected environment ID,
model ID, AWS region, `Converse` or `ConverseStream` operation, and dedicated
credentials-profile name. It also binds the current application version,
immutable release-image digest, provider-call latency objective, maximum
profile age, and maximum report age.

The profile is a reviewed input, not an API request. It must be a current-user
owned mode-`0600` regular non-symlink file outside the repository and release
bundle. Unknown fields and a release version other than the executing IIP
version fail closed.

The separate AWS shared-credentials file must have exactly one section whose
name equals `credentialsProfile` and exactly these keys:

- `aws_access_key_id`;
- `aws_secret_access_key`; and
- `aws_session_token`.

This shape follows the AWS shared-credentials format and prevents use of a
two-part long-lived access key. It does not validate the session's actual
expiration or IAM policy. The qualifier never hashes, prints, copies into the
report, or passes those values as Docker environment variables.

## Underlying live evidence

The isolated pinned compatibility image makes exactly one fixed synthetic
request directly to AWS. `ConverseStream` must be consumed through its final
usage event before the official span completes. Both operations must expose
provider cache-read/cache-write meters and normalize Bedrock's uncached input
counter into OpenTelemetry total-input semantics before encoding the actual
span as OTLP and passing it through the real IIP receiver and ingestion
service. A deliberately failing asynchronous exporter must not change the
successful provider result.

The protected `BedrockInstrumentationCompatibilityReport` must say
`live-provider-interoperability`, match the selected target and current clean
source, carry the exact pinned boto3, botocore, OpenTelemetry instrumentation,
usage adapter, and SDK versions, and record a provider-call duration within
the reviewed objective. Offline Stubber evidence cannot satisfy this contract.

## Minimized report

`CustomerBedrockQualificationReport` binds the canonical profile, protected
environment, exact target, underlying live report, current source, application
version, and immutable image by SHA-256. It retains:

- `Converse` or `ConverseStream`, but not model or region;
- one provider call and one normalized usage record;
- provider-call latency, but not token quantities;
- 20 closed pass/fail checks, including cache-meter and total-input semantics;
- exact generation and expiry times; and
- six explicit non-claims.

The report prohibits the environment ID, credentials-profile name, model ID,
region, credential material, prompt, response, request ID, raw span, and raw
provider payload. Its content-derived ID covers every retained metadata and
specification field. The offline verifier revalidates both protected inputs,
the complete underlying report, current clean source, image digest, bindings,
identity, and expiry.

Successful evidence proves only the selected call and instrumentation profile.
It verifies cache meters when the selected response contains them but still
leaves reasoning usage unresolved. It does not prove credential
expiry/revocation or IAM least authority, model quality/safety, exact cost,
price authority, invoice agreement, deployed Collector/PKI transport,
sustained load, quota behavior, regional HA, or any other model, region,
operation, or provider API.
