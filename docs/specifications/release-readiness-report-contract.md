# Release readiness report contract

**Status:** v1alpha1 executable aggregate local evidence

`ReleaseReadinessReport` answers one deliberately narrow question: does an
exact release bundle have the complete repository-controlled evidence set
required to call it a locally qualified candidate? It does not answer whether
the candidate is ready for a customer production environment or public
promotion.

The report verifies the bundle first, binds every evidence document to the
bundle revision, requires clean source evidence, validates each document with
its owning JSON Schema, checks its closed successful status and profile, and
retains only the document kind, content digest, observed status, revision, and
qualification boundary. Paths, endpoints, repositories, credentials, tenants,
customer identifiers, raw scanner findings, provider payloads, and test output
are not retained.

## Required local evidence

The `local-candidate-only` profile has 19 ordered requirements:

1. packaged install and N-1 release qualification;
2. exact-SBOM vulnerability qualification;
3. PostgreSQL-backed investigation capacity;
4. credential-broker compatibility;
5. OIDC issuer compatibility;
6. OIDC browser PKCE compatibility;
7. external policy-engine compatibility;
8. OTLP receiver compatibility;
9. Bedrock `Converse` offline instrumentation compatibility;
10. Bedrock `ConverseStream` offline instrumentation compatibility;
11. OpenAI chat-completions offline instrumentation compatibility;
12. complete local multi-provider AI FinOps runtime compatibility;
13. PostgreSQL logical recovery;
14. PostgreSQL physical continuity and PITR;
15. multi-node Kubernetes planned-disruption API availability, durable receiver
    intake, and workflow completion;
16. protected GitHub context compatibility;
17. plugin runtime compatibility;
18. current `production-core-v2` static configuration preflight, including
    verified PostgreSQL transport; and
19. current `production-ai-finops-v1` static configuration preflight, including
    verified PostgreSQL transport.

Missing evidence is `missing`. Malformed, dirty, unsuccessful, wrong-profile,
wrong-version, or wrong-revision evidence is `rejected`. Only 19 passed entries
produce `locally-qualified`; every other combination is `incomplete`.

The first two reports must additionally bind the exact release-manifest digest,
version, and revision. The AI FinOps runtime report must bind the candidate
application version. Deployment preflight reports must bind the candidate
application and chart versions. Offline provider evidence remains visibly
`offline-provider`; static Helm evidence remains visibly
`configuration-only`.

The historical `production-core-v1` and `production-ai-finops-v0` preflight
shapes remain independently readable under their owning contract, but they are
rejected as wrong-profile evidence by current local release readiness. This
prevents a pre-transport-security report from qualifying a new candidate.

## External gates

Every report contains the same eight `external-required` gates: approved
registry publication, organizational signatures, customer install preflight,
customer ingress availability, customer integration interoperability, live AI
provider and price authority, design-partner acceptance, and legal/brand/
governance decisions. These entries are scope boundaries, not failed local
tests, and are never caller-overridable acknowledgements.

The additive `CustomerDeploymentQualificationReport` can now provide a
machine-verifiable chain for the narrow live-install and customer-ingress
portion of those external gates. It does not mutate this local readiness
report or satisfy the other customer and organizational gates.

The aggregate report does not replace the owning report verifiers, create a
release approval, publish an artifact, sign an image, access a customer
cluster, or broaden any integration credential. It remains outside the
immutable release bundle and can be regenerated after transporting the bundle
and evidence set.
