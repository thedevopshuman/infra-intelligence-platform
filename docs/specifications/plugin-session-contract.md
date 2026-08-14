# Plugin session and handshake contract

**Status:** v1alpha1

`PluginSession` is the host's bounded handshake result after manifest validation and policy evaluation. Granted capabilities are always a subset of manifest declarations. The document contains only a capability-token reference and digest, never bearer material. Expiry, cancellation, request count, wall time, and output limits are mandatory.

The contract does not imply in-process trust. A production runner still enforces digest/signature verification, process isolation, network and filesystem policy, CPU/memory limits, and request-scoped credential delivery.
