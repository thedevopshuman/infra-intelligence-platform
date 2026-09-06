# Customer OTLP receiver qualification

**Status:** Executable opt-in customer-environment gate

Use this gate after installing an immutable release and configuring separate
metrics, logs, and metadata-only GenAI trace channels on its isolated receiver.
The protected profile values must match those channel allowlists exactly.

The command starts a temporary digest-pinned Collector through Docker Desktop.
It does not change the Kubernetes cluster, read Kubernetes Secrets, or reuse
the customer's long-running Collector. It first sends one empty direct,
redirect-denying request per signal, which the IIP receiver treats as an
authenticated no-op, and then sends three synthetic items through the
Collector. The temporary Collector and its ephemeral queue are removed
afterward.

Prepare mode-`0600` profile, API/channel credential, and client-key files. All
four credential values must differ. Then run:

```bash
IIP_CUSTOMER_OTLP_ALLOW_OBSERVATION=true \
IIP_CUSTOMER_OTLP_PROFILE=/absolute/protected/customer-otlp-profile.json \
IIP_CUSTOMER_OTLP_API_BASE_URL=https://iip.example.com \
IIP_CUSTOMER_OTLP_RECEIVER_ENDPOINT=https://otlp.iip.example.com:4318 \
IIP_CUSTOMER_OTLP_API_TOKEN_FILE=/absolute/protected/api-token \
IIP_CUSTOMER_OTLP_API_CA_FILE=/absolute/protected/api-ca.pem \
IIP_CUSTOMER_OTLP_METRICS_TOKEN_FILE=/absolute/protected/metrics-token \
IIP_CUSTOMER_OTLP_LOGS_TOKEN_FILE=/absolute/protected/logs-token \
IIP_CUSTOMER_OTLP_TRACES_TOKEN_FILE=/absolute/protected/traces-token \
IIP_CUSTOMER_OTLP_RECEIVER_CA_FILE=/absolute/protected/receiver-ca.pem \
IIP_CUSTOMER_OTLP_CLIENT_CERTIFICATE_FILE=/absolute/protected/collector.crt \
IIP_CUSTOMER_OTLP_CLIENT_KEY_FILE=/absolute/protected/collector.key \
IIP_CUSTOMER_OTLP_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
  make qualify-customer-otlp-receiver PYTHON=.venv/bin/python
```

Omit `IIP_CUSTOMER_OTLP_API_CA_FILE` only when the API certificate chains to
system trust. The receiver CA is always explicit. The result is written to
`dist/customer-otlp-receiver-qualification-report.json`. A safe but
incompatible observation writes `not-qualified` evidence and exits nonzero;
unsafe files, dirty source, crossed targets, or unavailable Docker fail without
manufacturing evidence.

Rebind a retained report without sending telemetry:

```bash
make verify-customer-otlp-receiver-qualification-report \
  PYTHON=.venv/bin/python
```

Supply the same profile, API/receiver endpoints, CA files, public client
certificate, and image variables. Verification deliberately does not require
or hash the channel credentials or private key.

The report is safe to share only under the customer's release-evidence policy.
The profile and source artifacts remain protected. The gate does not qualify
the customer's permanent Collector configuration, application
instrumentation, queue capacity, storage recovery, certificate lifecycle,
sustained load, or regional failover.
