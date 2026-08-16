# API rollout recovery

1. Confirm the affected Deployment, current image digest, desired replicas, and unavailable Pods.
2. Inspect warning Events and bounded error logs for the new revision.
3. Compare the current revision with the last healthy revision.
4. If rollback is appropriate, create a governed action proposal and require approval before mutation.
5. Verify available replicas, request health, and error rate after any approved action.

This document is operational context, not executable authority. Never place credentials or secrets in runbooks.
