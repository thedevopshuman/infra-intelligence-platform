"""Safe reference action executors."""

from __future__ import annotations

from typing import Mapping

from iip.application.ports import ActionExecutionOutcome, ActorContext


class KubernetesRestartDryRunExecutor:
    """Validate the narrow Kubernetes restart shape without performing mutation."""

    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        del actor, approval_id
        spec = proposal.get("spec")
        if not isinstance(spec, Mapping) or spec.get("dryRun") is not True:
            raise RuntimeError("action.executor.live-disabled")
        parameters = spec.get("parameters")
        if not isinstance(parameters, Mapping):
            raise RuntimeError("action.executor.parameters-invalid")
        namespace = parameters.get("namespace")
        kind = parameters.get("workloadKind")
        name = parameters.get("workloadName")
        if (
            not isinstance(namespace, str)
            or not isinstance(kind, str)
            or not isinstance(name, str)
            or kind not in ("deployment", "statefulset", "daemonset")
        ):
            raise RuntimeError("action.executor.parameters-invalid")
        return ActionExecutionOutcome(
            outcome="dry-run",
            provider="kubernetes",
            operation_ref=f"kubernetes://local/namespaces/{namespace}/{kind}s/{name}",
            verification_status="not-run",
            verification_summary=(
                "The approved restart request passed reference dry-run validation; "
                "no Kubernetes mutation was performed."
            ),
        )
