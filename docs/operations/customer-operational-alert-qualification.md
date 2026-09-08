# Customer operational-alert qualification

**Status:** Executable read-only customer workflow

**Contract:** [Customer operational-alert qualification contracts](../specifications/customer-operational-alert-qualification-contract.md)

This workflow proves that one customer monitoring path loads the selected IIP
rules, sees component heartbeats, evaluates a synthetic rule, and delivers both
firing and recovery notifications through one exact route. The qualifier reads
only; it never installs rules, posts alerts, changes routing, or stops IIP.

## Customer-owned preparation

1. Install the production operational-alert overlay and confirm the customer
   evaluator selects its namespace and labels.
2. Copy
   `contracts/examples/customer-operational-alert-qualification-profile.json`
   outside Git, replace every example value, copy the exact
   `clusterBindingDigest` and `namespaceBindingDigest` from the current customer
   deployment qualification, and set mode `0600`.
3. Create three least-privilege read credentials: Prometheus API,
   Alertmanager status, and receipt API. Store each in a different mode-`0600`
   file. Prepare the three reviewed CA bundles.
4. Configure the selected private notification route to send the two synthetic
   lifecycle states to a customer receipt service. The receipt service must
   expose the closed JSON shape in the contract and require its own credential.
5. In a separate `iip.qualification` group, create the temporary alert below.
   Use only the non-sensitive profile probe/route values in its labels:

```yaml
groups:
  - name: iip.qualification
    rules:
      - alert: IIPQualificationSynthetic
        expr: vector(1)
        for: 0m
        labels:
          iip_qualification_probe: iip-alert-route-20260908
          iip_qualification_route: platform-primary
          severity: warning
        annotations:
          summary: IIP operational alert route qualification
```

Wait for the firing receipt. Then change the expression to
`vector(0) > 0`, wait for the resolved receipt, and leave the inactive healthy
rule loaded while running the qualifier. Remove the temporary rule after the
report is retained. These rule changes belong to the customer's monitoring
owner and normal change process.

## Run

Use the exact immutable image digest installed in the customer environment:

```bash
IIP_CUSTOMER_OPERATIONAL_ALERT_ALLOW_OBSERVATION=true \
IIP_CUSTOMER_OPERATIONAL_ALERT_PROFILE=/protected/operational-alert-profile.json \
IIP_CUSTOMER_OPERATIONAL_ALERT_PROMETHEUS_TOKEN_FILE=/protected/prometheus-token \
IIP_CUSTOMER_OPERATIONAL_ALERT_ALERTMANAGER_TOKEN_FILE=/protected/alertmanager-token \
IIP_CUSTOMER_OPERATIONAL_ALERT_RECEIPT_TOKEN_FILE=/protected/receipt-token \
IIP_CUSTOMER_OPERATIONAL_ALERT_PROMETHEUS_CA_FILE=/protected/prometheus-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_ALERTMANAGER_CA_FILE=/protected/alertmanager-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_RECEIPT_CA_FILE=/protected/receipt-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_IMAGE_DIGEST=sha256:... \
make qualify-customer-operational-alerts PYTHON=.venv/bin/python
```

The explicit observation switch records operator intent to read live customer
monitoring metadata. A non-qualified run still writes a minimized report and
exits `1`; unsafe inputs, a dirty checkout, or contract errors exit `2`.

Rebind retained evidence from the same clean checkout:

```bash
IIP_CUSTOMER_OPERATIONAL_ALERT_PROFILE=/protected/operational-alert-profile.json \
IIP_CUSTOMER_OPERATIONAL_ALERT_PROMETHEUS_CA_FILE=/protected/prometheus-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_ALERTMANAGER_CA_FILE=/protected/alertmanager-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_RECEIPT_CA_FILE=/protected/receipt-ca.pem \
IIP_CUSTOMER_OPERATIONAL_ALERT_IMAGE_DIGEST=sha256:... \
make verify-customer-operational-alert-qualification-report PYTHON=.venv/bin/python
```

Retain the protected profile, CA bundles, and minimized report under the
customer evidence policy. Do not place them, endpoint credentials, route
configuration, receipt payloads, contact details, or screenshots in Git.

## Interpreting the result

A qualified report proves the selected production rules were loaded and
healthy, all expected component heartbeat jobs were queryable, the synthetic
rule was loaded and healthy, the router was ready, and the receipt service
observed a timely firing/resolved pair.

It does not prove that every production condition will fire under a real
incident. Continue to monitor the Collector, Prometheus-compatible backend,
Alertmanager, and receipt/notification destination independently. Test other
routes, silences, inhibition, escalation, HA, retention, regional behavior,
and human acknowledgement under their owning procedures.
