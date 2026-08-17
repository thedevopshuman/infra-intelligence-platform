# Investigation completion SLO report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/investigation-completion-slo-report.schema.json`

`InvestigationCompletionSloReport` is the authenticated, tenant-scoped rolling objective for useful asynchronous investigation completion. It measures each durable job once from queue acceptance to terminal state.

The deployment-configured window ends at `metadata.evaluatedAt`. A job is `immature` until `maximumCompletionSeconds` has elapsed. The remaining jobs form the eligible cohort:

- `withinObjectiveJobs` reached durable `completed` state at or before their individual deadline;
- `lateCompletedJobs` reached `completed` after that deadline;
- `failedJobs` terminalized as failed;
- `cancelledJobs` terminalized as cancelled;
- `unfinishedJobs` remain queued, running, or cancellation-requested at evaluation time. A terminal transition committed after the evaluation boundary is reconstructed as unfinished rather than treated as corrupt state.

`eligibleJobs` equals the sum of within-objective, late-completed, failed, cancelled, and unfinished jobs. `acceptedJobs` equals eligible plus immature jobs. `attainmentBasisPoints` is `null` when the eligible cohort is empty; otherwise it is `floor(withinObjectiveJobs * 10000 / eligibleJobs)`.

`no-data` means there is no mature cohort. `insufficient-data` means the cohort is smaller than `minimumEligibleJobs`. Once the minimum is reached, `meeting` means attainment is at least `minimumAttainmentBasisPoints`; otherwise the status is `breached`.

The report covers the durable asynchronous dispatch boundary. It contains aggregate counts only—never investigation identity or request content, prompts, evidence, findings, worker identity, credentials, provider output, or exceptions.

## Authority

`GET /v1/operations/investigations/completion-slo` requires Bearer authentication, the `platform-admin` role, and policy approval for `investigation-completion-slo:read`. Tenant and objective values come from authenticated and deployment context; the endpoint accepts no query parameters.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | The endpoint received any query field. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The platform-admin role or policy authorization is absent. |
| `503` | `investigation.completion-slo.unavailable` | Storage returned inconsistent state or was unavailable. |
