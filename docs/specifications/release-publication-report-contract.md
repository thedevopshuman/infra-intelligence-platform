# Release publication report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/release-publication-report.schema.json`

`ReleasePublicationReport` is source-bound evidence that both verified OCI
indexes from one immutable release bundle were copied to a registry without
changing their index digests. The targets are ordered and closed: the control
plane and trusted plugin-mediation bridge each retain their bundle-derived
digest, version tag, and customer-installable `repository@sha256:...`
reference.

The `rpr_` identifier is derived from the source binding, release, channel,
environment, targets, and checks. `generatedAt` is excluded so an idempotent
retry of the same publication has the same evidence identity.

The report deliberately says `published-unsigned`. A version tag is a
discovery convenience and never becomes an installation or verification
identity. Production promotion still requires an exact organizational
signature report, current SBOM vulnerability qualification, and the applicable
environment qualification reports.

The report contains public repository references but no registry credential,
OIDC token, signature material, Docker configuration path, raw command output,
or provider error text. A partial or digest-mismatched publication emits no
success report. Report paths are create-once: a retry cannot overwrite or
remove retained evidence and must select a new path if one already exists.
