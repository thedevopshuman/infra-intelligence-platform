# ADR 0101: retain source-bound PostgreSQL logical recovery evidence

**Status:** Accepted  
**Date:** 2026-09-05

## Context

ADR 0010 established a useful Docker backup/restore experiment and ADR 0049
added a least-authority scheduled logical dump. The experiment printed a loose
operations document and the repository retained one historical copy. That copy
could not be validated as a public contract, was not bound to a source revision
or current migration, disclosed an unnecessary fixture tenant label, and could
be mistaken for current release evidence after the schema evolved.

Local logical recovery is still only one input to production continuity. A
strict report must improve traceability without turning a quiesced Docker run
into an unsupported HA, point-in-time recovery, or customer RPO/RTO claim.

## Decision

Define the closed `PostgreSQLRecoveryQualificationReport` contract and make the
existing disposable experiment its only writer and semantic verifier.

The report:

- binds the observation to the exact Git revision, dirty state, application
  version, latest packaged migration, PostgreSQL version, pinned database image,
  Docker version, Python version, and host platform;
- records only aggregate fixture counts, timings, per-table counts and digests,
  an aggregate sequence digest, and projection-verification measurements;
- uses one closed ordered check set derived from measured facts;
- emits `qualified` only when complete-state, source-stability, isolated-restore,
  integrity, projection, and local timing conditions all pass;
- may retain a `failed` report for a timing-objective miss, while returning a
  non-zero command status;
- supports offline verification and an optional clean-current-source promotion
  check; and
- lives under `dist/` as environment evidence rather than modifying the source
  tree or immutable release bundle after the run.

The committed historical measurement is removed. The versioned contract example
is illustrative and explicitly not operational evidence.

## Consequences

Recovery claims are now machine-readable, source-bound, minimized, and
repeatable. An old report cannot silently represent a later schema, and clean
release evidence can be verified without rerunning Docker.

The local profile remains deliberately narrow. Production PostgreSQL selection
must add separately measured continuous-write, replica/failover, PITR, protected
storage, disaster-recovery, data-volume, and customer-objective profiles. A
vendor questionnaire or manually asserted checklist is not executable evidence.

