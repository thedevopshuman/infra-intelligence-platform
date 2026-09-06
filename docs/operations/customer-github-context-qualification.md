# Customer GitHub context qualification

This optional host-side gate proves that one reviewed GitHub Cloud or GitHub
Enterprise Server document can be read at an exact commit and Git blob through
the shipped production context adapter and an already-qualified customer
credential broker. Run it only from an authorized administrative workstation;
it performs one real repository read.

## Prepare the protected inputs

1. Copy and edit the GitHub profile:

   ```sh
   cp contracts/examples/customer-github-context-qualification-profile.json /secure/iip/github-profile.json
   chmod 600 /secure/iip/github-profile.json
   ```

2. Export the exact protected integration configuration used by the intended
   runtime, reduce the qualification snapshot to one integration/repository/
   document, and protect it. The checked example shows the required shape:

   ```sh
   cp deploy/context/customer-github-context-qualification-integrations.example.json /secure/iip/github-integrations.json
   chmod 600 /secure/iip/github-integrations.json
   ```

3. Create a separate credential-broker qualification from
   `customer-credential-broker-qualification-profile-github-context.json`.
   Its base case must exactly match the GitHub profile. Do not reuse a broker
   report for Kubernetes, metrics, or another integration merely because it
   came from the same broker.

4. Make the broker workload-identity token a current-user owned mode-`0600`
   regular file. Supply the broker CA. For GitHub Enterprise Server, supply
   the GitHub CA when the integration snapshot declares a custom CA; omit it
   when that snapshot uses system trust. GitHub Cloud always uses system trust.

The profile, integration configuration, and broker profile contain customer
identifiers and must not be placed in `dist/` or committed. The report is safe
to transport only after its verifier succeeds.

## Qualify

```sh
make qualify-customer-github-context \
  IIP_CUSTOMER_GITHUB_CONTEXT_ALLOW_OBSERVATION=true \
  IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE=/secure/iip/github-profile.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG=/secure/iip/github-integrations.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE=/secure/iip/github-ca.pem \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT=/secure/iip/github-broker-report.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE=/secure/iip/github-broker-profile.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT=https://credential-broker.example.com \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_WORKLOAD_TOKEN_FILE=/secure/iip/workload-token \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE=/secure/iip/broker-ca.pem \
  IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST=sha256:REPLACE_WITH_64_HEX
```

For GitHub Cloud or Enterprise Server using public/system trust, omit
`IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE` and set `caBundlePath` to `null`
in the exact integration snapshot. The command refuses dirty source, an
unpinned image, a stale/future profile, crossed inputs, an unqualified broker
report, or missing explicit enablement.

## Verify retained evidence

The offline verifier makes no GitHub request and needs no workload token. It
revalidates the report and broker prerequisite against current clean source and
every retained input binding:

```sh
make verify-customer-github-context-qualification-report \
  IIP_CUSTOMER_GITHUB_CONTEXT_PROFILE=/secure/iip/github-profile.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_INTEGRATION_CONFIG=/secure/iip/github-integrations.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_GITHUB_CA_FILE=/secure/iip/github-ca.pem \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_REPORT=/secure/iip/github-broker-report.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_PROFILE=/secure/iip/github-broker-profile.json \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_ENDPOINT=https://credential-broker.example.com \
  IIP_CUSTOMER_GITHUB_CONTEXT_BROKER_CA_FILE=/secure/iip/broker-ca.pem \
  IIP_CUSTOMER_GITHUB_CONTEXT_IMAGE_DIGEST=sha256:REPLACE_WITH_64_HEX
```

Any changed profile, configuration, CA, broker evidence, source revision, or
image fails verification. The report does not qualify interactive installation
or lifecycle of a GitHub App/token, organizational repository controls,
sustained rate limits, certificate rotation, network/service HA, or any target
beyond the one selected document.
