# Tenant evidence redaction

**Status:** Executable additive privacy control; built-in credential redaction remains mandatory

IIP always inspects supported Evidence artifacts for structured credential
fields, secret-like assignments, Bearer values, and private keys before
hashing or persistence. An optional tenant policy can additionally remove
email and validated IPv4 values from selected exact evidence types.

## Prepare a policy set

Copy `deploy/evidence-redaction-policies.example.json` to a protected
configuration repository. Replace the example tenant, version, rules, and
policy ID. Rules, evidence types, value classes, and the outer policies array
must remain lexically sorted; one evidence type may appear only once per
policy.

Calculate the content-derived ID with the Python adapter during review:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
from iip.adapters.evidence_redaction import derive_evidence_redaction_policy_id

document = json.loads(Path("/protected/evidence-redaction-policy.json").read_text())
print(derive_evidence_redaction_policy_id(document))
PY
```

The policy contains field classes and scope, not credentials or matched data,
but it reveals the organization's privacy posture and should still follow
reviewed change control. Do not place literal customer values or invented
regular expressions in it; the closed contract rejects both.

## Local runtime

Supply the exact wrapper as protected process configuration:

```bash
export IIP_EVIDENCE_REDACTION_POLICIES_JSON="$(tr -d '\n' < /protected/evidence-redaction-policies.json)"
make dev-up PYTHON=.venv/bin/python
```

Malformed or duplicate policy configuration prevents startup. Removing the
variable disables only the optional detectors; mandatory credential redaction
continues.

## Helm

Create one Secret containing the reviewed wrapper and reference it from values:

```bash
kubectl -n iip-system create secret generic iip-evidence-redaction \
  --from-file=evidence-redaction-policies-json=/protected/evidence-redaction-policies.json
```

```yaml
evidenceRedaction:
  policiesExistingSecret: iip-evidence-redaction
  policiesSecretKey: evidence-redaction-policies-json
```

The chart injects the same Secret key into the API, workflow worker, and
isolated OTLP receiver because all three can produce Evidence. The deployment
preflight records only that a custom policy is configured and, in explicit
cluster mode, verifies the Secret/key exists; it never reads policy values.

Rotate by writing a newly versioned, newly identified policy set to the owning
secret system and rolling all enabled evidence-producing workloads together.
Existing Evidence retains its prior bytes, digest, methods, and policy
reference.

## Verify behavior

Run the focused policy and deployment tests:

```bash
make test-evidence-redaction PYTHON=.venv/bin/python
```

Collect representative evidence for every selected type in a non-production
tenant. Confirm `spec.handling.redaction.policyRef`, method names, useful
investigation behavior, and absence of prohibited values from the protected
artifact store. A `policyRef` with `not-required` means the policy was selected
but no detector matched.

This feature does not inspect unsupported binary media, infer arbitrary PII,
or prove regulatory compliance. Filter at the source/Collector first, retain
only necessary evidence, and keep sensitivity, retention, residency, and
artifact-read authorization independently configured.
