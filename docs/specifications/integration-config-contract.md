# Integration configuration contract

**Status:** v1alpha1

`IntegrationConfig` records tenant-scoped provider setup without embedding credentials. `credentialRef` is an opaque broker reference; the referenced secret is resolved only for one authorized provider request. Collection scope, schedule, and declared read permissions are reviewable independently of secret material.

The Kubernetes example is reconciliation-first and read-only. Configuration parameters are validated by the selected plugin. Disabling an integration prevents new collection sessions but does not erase historical resources, events, evidence, or audit records.
