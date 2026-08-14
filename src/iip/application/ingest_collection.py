"""Host-side validation and ingestion for public resource collection results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import ActorContext, SourceCheckpoint, SourceCheckpointRepository
from iip.domain.models import Resource


class InvalidCollectionError(ValueError):
    """A request/result pair violated host-enforced collection invariants."""


@dataclass(frozen=True)
class IngestCollectionCommand:
    """One authenticated observer result and the request that bounded it."""

    actor: ActorContext
    request: Mapping[str, Any]
    result: Mapping[str, Any]
    correlation_id: str | None = None


class ResourceCollectionIngestionService:
    """Validate a complete plugin response before mutating platform state."""

    def __init__(
        self,
        ingestion: ResourceIngestionService,
        checkpoints: SourceCheckpointRepository,
    ) -> None:
        self._ingestion = ingestion
        self._checkpoints = checkpoints

    def execute(self, command: IngestCollectionCommand) -> tuple[Resource, ...]:
        request_metadata, request_spec, result_metadata, result_spec = self._validate(
            command
        )
        observations = result_spec["observations"]
        completion = result_spec["completion"]
        accepted = []
        for payload in observations:
            accepted.append(
                self._ingestion.execute(
                    IngestResourceCommand(
                        actor=command.actor,
                        payload=payload,
                        correlation_id=command.correlation_id,
                    )
                )
            )

        if completion["status"] == "complete":
            # For an empty initial snapshot there is no observation sequence to
            # attach. Sequence zero is the neutral persisted floor; callers use
            # result.nextSequence as their next requested sequence.
            sequence = max(request_spec["startSequence"], completion["nextSequence"] - 1)
            self._checkpoints.commit_checkpoint(
                SourceCheckpoint(
                    tenant_id=request_metadata["tenantId"],
                    source_id=request_spec["sourceId"],
                    stream_id=request_spec["streamId"],
                    sequence=sequence,
                    checkpoint=completion["checkpoint"],
                    committed_at=result_metadata["createdAt"],
                ),
                mode=request_spec["mode"],
            )
        return tuple(accepted)

    @staticmethod
    def _validate(
        command: IngestCollectionCommand,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            request = dict(command.request)
            result = dict(command.result)
            if request.get("apiVersion") != "iip.platform/v1alpha1" or request.get(
                "kind"
            ) != "ResourceCollectionRequest":
                raise KeyError
            if result.get("apiVersion") != "iip.platform/v1alpha1" or result.get(
                "kind"
            ) != "ResourceCollectionResult":
                raise KeyError
            request_metadata = dict(request["metadata"])
            request_spec = dict(request["spec"])
            result_metadata = dict(result["metadata"])
            result_spec = dict(result["spec"])
            completion = dict(result_spec["completion"])
            observations = result_spec["observations"]
            scope = request_spec["scope"]
            limits = request_spec["limits"]
        except (KeyError, TypeError, ValueError):
            raise InvalidCollectionError("collection.contract.invalid") from None

        if (
            request_metadata.get("tenantId") != command.actor.tenant_id
            or request_metadata.get("actorId") != command.actor.actor_id
            or result_metadata.get("tenantId") != command.actor.tenant_id
            or result_metadata.get("requestId") != request_metadata.get("requestId")
            or result_metadata.get("sourceId") != request_spec.get("sourceId")
        ):
            raise InvalidCollectionError("collection.scope.mismatch")
        if not isinstance(observations, list) or not isinstance(limits, Mapping):
            raise InvalidCollectionError("collection.contract.invalid")
        if (
            completion.get("resourceCount") != len(observations)
            or completion.get("nextSequence")
            != request_spec.get("startSequence", -1) + len(observations)
            or len(observations) > limits.get("maxResources", -1)
        ):
            raise InvalidCollectionError("collection.result.inconsistent")
        encoded_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        )
        if encoded_size > limits.get("maxOutputBytes", -1):
            raise InvalidCollectionError("collection.result.output-limited")
        scope_digest = "sha256:" + hashlib.sha256(
            json.dumps(scope, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        if completion.get("scopeDigest") != scope_digest:
            raise InvalidCollectionError("collection.scope.digest-mismatch")

        status = completion.get("status")
        if status == "complete":
            if not isinstance(completion.get("checkpoint"), str) or completion.get(
                "reasonCode"
            ) is not None:
                raise InvalidCollectionError("collection.completion.invalid")
        elif status in ("partial", "failed", "cancelled"):
            if not isinstance(completion.get("reasonCode"), str) or completion.get(
                "checkpoint"
            ) is not None:
                raise InvalidCollectionError("collection.completion.invalid")
        else:
            raise InvalidCollectionError("collection.completion.invalid")

        if request_spec.get("mode") == "reconciliation":
            if completion.get("snapshotId") != request_spec.get("snapshotId"):
                raise InvalidCollectionError("collection.snapshot.mismatch")
        elif completion.get("snapshotId") is not None:
            raise InvalidCollectionError("collection.snapshot.mismatch")
        try:
            created = datetime.fromisoformat(
                str(result_metadata["createdAt"]).replace("Z", "+00:00")
            )
            requested = datetime.fromisoformat(
                str(request_metadata["requestedAt"]).replace("Z", "+00:00")
            )
            deadline = datetime.fromisoformat(
                str(request_spec["deadline"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            raise InvalidCollectionError("collection.time.invalid") from None
        if not requested <= created <= deadline:
            raise InvalidCollectionError("collection.time.invalid")

        for index, observation in enumerate(observations):
            try:
                metadata = observation["metadata"]
                cursor = metadata["observation"]
            except (KeyError, TypeError):
                raise InvalidCollectionError("collection.observation.invalid") from None
            if (
                metadata.get("tenantId") != command.actor.tenant_id
                or cursor.get("sourceId") != request_spec.get("sourceId")
                or cursor.get("streamId") != request_spec.get("streamId")
                or cursor.get("mode") != request_spec.get("mode")
                or cursor.get("sequence") != request_spec["startSequence"] + index
                or cursor.get("snapshotId") != request_spec.get("snapshotId")
                or "checkpoint" in cursor
            ):
                raise InvalidCollectionError("collection.observation.invalid")
        return (
            request_metadata,
            request_spec,
            result_metadata,
            {"observations": observations, "completion": completion},
        )
