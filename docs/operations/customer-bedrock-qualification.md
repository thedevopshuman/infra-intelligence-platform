# Customer Bedrock qualification

This optional customer-environment gate makes one real, billable Amazon
Bedrock request. It validates the direct application-to-provider path through
official OpenTelemetry botocore instrumentation and the pinned metadata-only
IIP usage adapter; it does not proxy model traffic. Use a clean checkout of the
exact release source and Docker Desktop or another compatible Docker engine.
Kubernetes is not required.

## Prepare the reviewed profile

Copy the example outside the repository and set the approved environment,
model, region, operation, application version, and immutable deployed image
digest:

```sh
cp contracts/examples/customer-bedrock-qualification-profile.json \
  /secure/iip/customer-bedrock-profile.json
chmod 600 /secure/iip/customer-bedrock-profile.json
```

Use `ConverseStream` when satisfying the AI FinOps V0 streaming prerequisite.
`Converse` evidence cannot be promoted into a streaming claim. The selected
model must support the chosen operation.

## Prepare one temporary credential

Create a dedicated AWS shared-credentials file outside the repository. The
file must contain exactly the named profile from the qualification profile and
the three temporary credential fields:

```ini
[iip-bedrock-qualification]
aws_access_key_id = REPLACE_WITH_TEMPORARY_ACCESS_KEY
aws_secret_access_key = REPLACE_WITH_TEMPORARY_SECRET_KEY
aws_session_token = REPLACE_WITH_TEMPORARY_SESSION_TOKEN
```

```sh
chmod 600 /secure/iip/customer-bedrock-credentials
```

AWS recommends temporary credentials and documents the shared-credentials
format in its [SDK settings reference](https://docs.aws.amazon.com/sdkref/latest/guide/file-format.html).
Use an approved role scoped to the exact inference target. AWS documents
`bedrock:InvokeModel` for `Converse` and
`bedrock:InvokeModelWithResponseStream` for `ConverseStream` in its
[inference prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-prereq.html).
A successful qualification call does not prove that the role lacks additional
authority; review the IAM policy separately.

Do not export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or
`AWS_SESSION_TOKEN`. The qualifier removes ambient AWS credential-provider and
endpoint-override variables, mounts only the dedicated file read-only, disables
instance-metadata lookup, and does not expose credential values through the
container environment.

## Run the explicitly enabled call

The command sends the fixed synthetic request already reviewed in the pinned
compatibility harness. It asks for one short response, disables message-content
capture, verifies cache counters and OTel total-input normalization, and
retains neither prompt nor response. Reasoning usage remains unresolved. The
call can incur a small AWS charge.

```sh
make qualify-customer-bedrock \
  IIP_CUSTOMER_BEDROCK_ALLOW_PROVIDER_CALL=true \
  IIP_CUSTOMER_BEDROCK_PROFILE=/secure/iip/customer-bedrock-profile.json \
  IIP_CUSTOMER_BEDROCK_AWS_CREDENTIALS_FILE=/secure/iip/customer-bedrock-credentials \
  IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST=sha256:REPLACE_WITH_64_HEX \
  IIP_CUSTOMER_BEDROCK_LIVE_REPORT=/secure/iip/customer-bedrock-live-report.json \
  IIP_CUSTOMER_BEDROCK_REPORT=dist/customer-bedrock-qualification-report.json
```

The protected underlying report retains the exact model and region and is
written mode `0600`. The transportable qualification report contains only
digests, operation class, bounded measurements, checks, validity, and
limitations. If Docker Desktop cannot mount `/secure/iip`, place the protected
files in a Docker-shared directory outside Git tracking.

## Verify retained evidence

The verifier makes no AWS or Docker call and needs no credential:

```sh
make verify-customer-bedrock-qualification-report \
  IIP_CUSTOMER_BEDROCK_PROFILE=/secure/iip/customer-bedrock-profile.json \
  IIP_CUSTOMER_BEDROCK_LIVE_REPORT=/secure/iip/customer-bedrock-live-report.json \
  IIP_CUSTOMER_BEDROCK_IMAGE_DIGEST=sha256:REPLACE_WITH_64_HEX \
  IIP_CUSTOMER_BEDROCK_REPORT=dist/customer-bedrock-qualification-report.json
```

Verification fails for changed or stale source, profile, target, underlying
evidence, release image, report identity, or validity. Run the separate
customer OTLP receiver qualification for Collector/PKI delivery and the
production catalog qualification for price authority. Delete the temporary AWS
credentials file after evidence verification or when the session expires.
