# Release qualification report contract

**Status:** v1alpha1 executable local release evidence

`ReleaseQualificationReport` binds successful packaged-install and supported
N-1 upgrade observations to one verified release manifest, exact source
revision, control-plane OCI index digest, packaged platform set, and measured
environment. It is written adjacent to the release bundle; it is never inserted
into the bundle after `SHA256SUMS` has been finalized.

The report has two closed profiles:

- `packaged-install` proves artifact verification, immutable deployment,
  authenticated runtime identity, controlled migrations, protected operations,
  explicit TLS ingress, checksum verification, isolated restore, and a second
  Helm revision.
- `n-minus-one-upgrade` proves strict source ancestry, both runtime identities,
  tenant-data preservation, non-regressing schema use, application rollback
  against the forward schema, idempotent re-upgrade, zero-failure authenticated
  Service reads, one blocked in-flight read surviving pod termination, and the
  expected four-revision Helm history.

Every check in a recorded profile is `passed`; a failed or interrupted gate
does not create a successful profile. A report containing one profile is
`incomplete`. Only exactly one of each required profile makes it `qualified`.
The verifier derives the summary, checks the fixed ordering and membership of
both check sets, recomputes the release-manifest digest from the verified
bundle, and verifies that runtime and upgrade target identities match the
candidate.

The availability measurement is internally derived: `attemptCount` equals
`successCount`, two authenticated HTTP reads are issued per successful attempt,
the base and target counts sum to the total, both releases are observed at
least ten times, and `failureCount` is zero. `inFlightDrain` records the three
separate facts that the database-blocked read was observed, exact pod
termination was requested, and the original request completed.

The current environment profile is deliberately `local-kind` with one tested
host platform, Kubernetes version, and Docker server version. It contains no
cluster identifier, tenant identifier, credential, endpoint, certificate, or
customer data. A local qualification report is not evidence for customer
ingress, managed PostgreSQL, regional failure, production load, or a different
runtime. Those environments must generate and retain their own profile under a
future additive contract.

`sourceDirty` is fixed to `false`. The recorder refuses a source revision that
does not match the bundle and refuses any dirty tracked or untracked source
state. The report is checksum-bound to an unsigned local bundle but is itself
unsigned; it proves repeatable test observations, not organizational publisher
identity.
