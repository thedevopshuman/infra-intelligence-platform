"""Host-side validation and ingestion for public resource collection results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import (
    ActorContext,
    Clock,
    ReconciliationRepository,
    ReconciliationSnapshot,
    ResourceRepository,
    SourceCheckpoint,
    SourceCheckpointRepository,
)
from iip.domain.models import ContractError, Resource


class InvalidCollectionError(ValueError):
    """A request/result pair violated host-enforced collection invariants."""


class CollectionConflictError(RuntimeError):
    """A valid collection conflicts with committed source state."""


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
        resources: ResourceRepository,
        checkpoints: SourceCheckpointRepository,
        reconciliations: ReconciliationRepository,
        clock: Clock,
    ) -> None:
        self._ingestion = ingestion
        self._resources = resources
        self._checkpoints = checkpoints
        self._reconciliations = reconciliations
        self._clock = clock

    def execute(self, command: IngestCollectionCommand) -> tuple[Resource, ...]:
        request_metadata, request_spec, result_metadata, result_spec = self._validate(
            command
        )
        observations = result_spec["observations"]
        completion = result_spec["completion"]
        provider_cursors = self._provider_cursors(completion.get("providerCursors"))
        resume = request_spec.get("resume")
        if resume is not None:
            current = self._checkpoints.get_checkpoint(
                command.actor.tenant_id, request_spec["sourceId"]
            )
            if (
                current is None
                or current.checkpoint != resume["checkpoint"]
                or current.provider_cursors
                != self._provider_cursors(resume["providerCursors"])
                or request_spec["startSequence"] != current.sequence + 1
            ):
                raise CollectionConflictError("collection.resume.stale")
            if bool(provider_cursors) != bool(current.provider_cursors):
                raise InvalidCollectionError("collection.cursor-state.invalid")
        try:
            parsed_observations = tuple(Resource.from_dict(item) for item in observations)
        except ContractError:
            raise InvalidCollectionError("collection.observation.invalid") from None

        mode = request_spec["mode"]
        result_digest = self._canonical_digest(command.result)
        previous = None
        present_uids: tuple[str, ...] = ()
        missing_uids: tuple[str, ...] = ()
        missing_resources: dict[str, Resource] = {}
        if mode == "reconciliation" and completion["status"] == "complete":
            present_uids = tuple(
                sorted(resource.identity.uid for resource in parsed_observations)
            )
            if len(present_uids) != len(set(present_uids)):
                raise InvalidCollectionError(
                    "collection.reconciliation.duplicate-resource"
                )
            previous = self._reconciliations.get_reconciliation(
                command.actor.tenant_id, request_spec["sourceId"]
            )
            if previous is not None and previous.snapshot_id == request_spec["snapshotId"]:
                if previous.result_digest != result_digest:
                    raise CollectionConflictError("collection.snapshot.conflict")
                checkpoint = self._checkpoints.get_checkpoint(
                    command.actor.tenant_id, request_spec["sourceId"]
                )
                if (
                    checkpoint is None
                    or checkpoint.stream_id != previous.stream_id
                    or checkpoint.sequence != previous.sequence
                    or checkpoint.checkpoint != previous.checkpoint
                    or checkpoint.provider_cursors != provider_cursors
                ):
                    raise CollectionConflictError("collection.snapshot.stale")
                return self._replayed_resources(
                    command.actor.tenant_id,
                    parsed_observations,
                    previous.tombstoned_uids,
                )
            if previous is not None and previous.scope_digest != completion["scopeDigest"]:
                raise CollectionConflictError("collection.scope.changed")
            missing_uids = tuple(
                sorted(set(previous.resource_uids).difference(present_uids))
                if previous is not None
                else ()
            )
            last_sequence = completion["nextSequence"] + len(missing_uids) - 1
            if last_sequence > 9_007_199_254_740_991:
                raise InvalidCollectionError("collection.sequence.exhausted")
            for resource_uid in missing_uids:
                current = self._resources.get(command.actor.tenant_id, resource_uid)
                if current is None:
                    raise InvalidCollectionError(
                        "collection.reconciliation.state-missing"
                    )
                missing_resources[resource_uid] = current

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
            committed_at = self._clock.now()
            if mode == "reconciliation":
                assert previous is None or isinstance(previous, ReconciliationSnapshot)
                last_sequence = completion["nextSequence"] + len(missing_uids) - 1
                for offset, resource_uid in enumerate(missing_uids):
                    current = missing_resources[resource_uid]
                    if current.lifecycle == "deleted":
                        accepted.append(current)
                        continue
                    accepted.append(
                        self._ingestion.execute(
                            IngestResourceCommand(
                                actor=command.actor,
                                payload=self._tombstone(
                                    current,
                                    observed_at=result_metadata["createdAt"],
                                    source_id=request_spec["sourceId"],
                                    stream_id=request_spec["streamId"],
                                    snapshot_id=request_spec["snapshotId"],
                                    sequence=completion["nextSequence"] + offset,
                                ),
                                correlation_id=command.correlation_id,
                            )
                        )
                    )
                # For an empty initial snapshot there is no observation sequence
                # to attach. startSequence is the neutral persisted floor.
                sequence = max(request_spec["startSequence"], last_sequence)
                checkpoint = self._checkpoint(
                    request_metadata,
                    request_spec,
                    completion,
                    sequence,
                    committed_at,
                )
                snapshot = ReconciliationSnapshot(
                    tenant_id=request_metadata["tenantId"],
                    source_id=request_spec["sourceId"],
                    stream_id=request_spec["streamId"],
                    snapshot_id=request_spec["snapshotId"],
                    scope_digest=completion["scopeDigest"],
                    sequence=sequence,
                    checkpoint=completion["checkpoint"],
                    result_digest=result_digest,
                    resource_uids=present_uids,
                    tombstoned_uids=missing_uids,
                    committed_at=committed_at,
                )
                try:
                    self._reconciliations.commit_reconciliation(snapshot, checkpoint)
                except ValueError:
                    raise CollectionConflictError(
                        "collection.checkpoint.conflict"
                    ) from None
            else:
                sequence = max(
                    request_spec["startSequence"], completion["nextSequence"] - 1
                )
                try:
                    self._checkpoints.commit_checkpoint(
                        self._checkpoint(
                            request_metadata,
                            request_spec,
                            completion,
                            sequence,
                            committed_at,
                        ),
                        mode=mode,
                    )
                except ValueError:
                    raise CollectionConflictError(
                        "collection.checkpoint.conflict"
                    ) from None
        return tuple(accepted)

    @staticmethod
    def _canonical_digest(document: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _checkpoint(
        request_metadata: Mapping[str, Any],
        request_spec: Mapping[str, Any],
        completion: Mapping[str, Any],
        sequence: int,
        committed_at: str,
    ) -> SourceCheckpoint:
        return SourceCheckpoint(
            tenant_id=request_metadata["tenantId"],
            source_id=request_spec["sourceId"],
            stream_id=request_spec["streamId"],
            sequence=sequence,
            checkpoint=completion["checkpoint"],
            committed_at=committed_at,
            provider_cursors=ResourceCollectionIngestionService._provider_cursors(
                completion.get("providerCursors")
            ),
        )

    @staticmethod
    def _provider_cursors(value: object) -> tuple[tuple[str, str], ...]:
        if value is None:
            return ()
        if (
            not isinstance(value, Mapping)
            or not 1 <= len(value) <= 2048
            or any(
                not isinstance(key, str)
                or not 1 <= len(key) <= 256
                or not isinstance(cursor, str)
                or not 1 <= len(cursor) <= 512
                for key, cursor in value.items()
            )
        ):
            raise InvalidCollectionError("collection.cursor-state.invalid")
        return tuple(sorted(value.items()))

    def _replayed_resources(
        self,
        tenant_id: str,
        observations: tuple[Resource, ...],
        tombstoned_uids: tuple[str, ...],
    ) -> tuple[Resource, ...]:
        resources = []
        for uid in tuple(item.identity.uid for item in observations) + tombstoned_uids:
            current = self._resources.get(tenant_id, uid)
            if current is None:
                raise InvalidCollectionError("collection.reconciliation.state-missing")
            resources.append(current)
        return tuple(resources)

    @staticmethod
    def _tombstone(
        resource: Resource,
        *,
        observed_at: str,
        source_id: str,
        stream_id: str,
        snapshot_id: str,
        sequence: int,
    ) -> dict[str, Any]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Resource",
            "metadata": {
                "uid": resource.identity.uid,
                "tenantId": resource.identity.tenant_id,
                "observedAt": observed_at,
                "observation": {
                    "sourceId": source_id,
                    "streamId": stream_id,
                    "sequence": sequence,
                    "mode": "reconciliation",
                    "snapshotId": snapshot_id,
                },
            },
            "spec": {
                "provider": resource.identity.provider,
                "type": resource.identity.resource_type,
                "externalId": resource.identity.external_id,
                "attributes": {},
                "relationships": [],
            },
            "status": {"lifecycle": "deleted"},
        }

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
            ) is not None or completion.get("providerCursors") is not None:
                raise InvalidCollectionError("collection.completion.invalid")
        else:
            raise InvalidCollectionError("collection.completion.invalid")

        if request_spec.get("mode") == "reconciliation":
            if completion.get("snapshotId") != request_spec.get("snapshotId"):
                raise InvalidCollectionError("collection.snapshot.mismatch")
        elif completion.get("snapshotId") is not None:
            raise InvalidCollectionError("collection.snapshot.mismatch")
        resume = request_spec.get("resume")
        if resume is not None:
            if not isinstance(resume, Mapping):
                raise InvalidCollectionError("collection.cursor-state.invalid")
            if not isinstance(resume.get("checkpoint"), str):
                raise InvalidCollectionError("collection.cursor-state.invalid")
            ResourceCollectionIngestionService._provider_cursors(
                resume.get("providerCursors")
            )
        ResourceCollectionIngestionService._provider_cursors(
            completion.get("providerCursors")
        )
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
