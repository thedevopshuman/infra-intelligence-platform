# Plugin session and handshake contract

**Status:** v1alpha1

`PluginSession` is the host's bounded handshake result after manifest validation and policy evaluation. Granted capabilities are always a subset of manifest declarations. The document contains only a capability-token reference and digest, never bearer material. Expiry, cancellation, request count, wall time, and output limits are mandatory. The cancellation endpoint is a control-plane URI template for the authenticated session owner; it is not sent to the plugin process.

The contract does not imply in-process trust. The first runner verifies the
manifest signature and image digest and enforces process, network, filesystem,
CPU, memory, PID, deadline, and output isolation. Network and secret declarations
require exact read-mediation grants; action declarations require exact
proposal-only grants. No profile gives the plugin a credential, approval, or
execution authority. Durable execution claims are mandatory for every profile.
