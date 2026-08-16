# Session context contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/session-context.schema.json`

`SessionContext` tells an authenticated client which tenant, actor, and roles the control plane derived from its credential. It exists so browser and SDK workflows can populate contract identity assertions without asking a user to type trusted identity fields.

The response is descriptive, not an authority grant. Application ports still receive the server-derived `ActorContext` and enforce tenant and role scope independently. A request payload or identity assertion header cannot change this context.

## API and security

`GET /v1/session` requires the normal control-plane Bearer credential and returns only the authenticated identity's non-secret identifiers and role names. It never returns the credential, its verifier, provider claims, credential expiry, integration credentials, or another actor's context.

The reference local authenticator has no issuance, rotation, or federation behavior. A production identity provider can replace it behind the authentication port without changing this contract.
