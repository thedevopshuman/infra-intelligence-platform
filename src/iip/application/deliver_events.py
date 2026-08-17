"""Lease-driven transactional-outbox delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass

from iip.application.ports import (
    EventOutbox,
    EventPublicationError,
    EventPublisher,
    PersistenceError,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_WORKER_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}")


@dataclass(frozen=True)
class EventDeliverySummary:
    """Bounded aggregate outcome for one exact tenant pass."""

    claimed: int = 0
    delivered: int = 0
    released: int = 0
    quarantined: int = 0
    ambiguous: int = 0


class EventDeliveryService:
    """Publish leased events at least once and acknowledge only owned leases."""

    def __init__(
        self,
        outbox: EventOutbox,
        publisher: EventPublisher,
        *,
        worker_id: str,
        batch_size: int = 100,
        lease_seconds: int = 30,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 300,
        max_attempts: int = 8,
    ) -> None:
        if (
            not isinstance(worker_id, str)
            or _WORKER_ID.fullmatch(worker_id) is None
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= 500
            or isinstance(lease_seconds, bool)
            or not 5 <= lease_seconds <= 300
            or isinstance(retry_base_seconds, bool)
            or not 1 <= retry_base_seconds <= 300
            or isinstance(retry_max_seconds, bool)
            or not retry_base_seconds <= retry_max_seconds <= 3600
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 1000
        ):
            raise ValueError("event.delivery.configuration.invalid")
        self._outbox = outbox
        self._publisher = publisher
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._max_attempts = max_attempts

    def run_once(self, tenant_id: str) -> EventDeliverySummary:
        if (
            not isinstance(tenant_id, str)
            or _TENANT_ID.fullmatch(tenant_id) is None
            or tenant_id == "*"
        ):
            raise ValueError("event.delivery.tenant.invalid")
        messages = tuple(
            self._outbox.claim_outbox(
                tenant_id,
                self._worker_id,
                limit=self._batch_size,
                lease_seconds=self._lease_seconds,
            )
        )
        delivered = released = quarantined = ambiguous = 0
        for message in messages:
            if message.event.tenant_id != tenant_id:
                raise PersistenceError("storage.corrupt")
            try:
                self._publisher.publish(message.event)
            except EventPublicationError:
                if message.attempts >= self._max_attempts:
                    did_quarantine = self._outbox.quarantine_outbox(
                        tenant_id,
                        self._worker_id,
                        message.message_id,
                        "event.publisher.unavailable",
                    )
                    quarantined += int(did_quarantine)
                    ambiguous += int(not did_quarantine)
                else:
                    did_release = self._outbox.release_outbox(
                        tenant_id,
                        self._worker_id,
                        message.message_id,
                        "event.publisher.unavailable",
                        retry_after_seconds=self._retry_delay(message.attempts),
                    )
                    released += int(did_release)
                    ambiguous += int(not did_release)
                continue
            did_acknowledge = self._outbox.acknowledge_outbox(
                tenant_id,
                self._worker_id,
                message.message_id,
            )
            delivered += int(did_acknowledge)
            ambiguous += int(not did_acknowledge)
        return EventDeliverySummary(
            claimed=len(messages),
            delivered=delivered,
            released=released,
            quarantined=quarantined,
            ambiguous=ambiguous,
        )

    def _retry_delay(self, attempts: int) -> int:
        exponent = min(max(attempts - 1, 0), 16)
        return min(self._retry_max_seconds, self._retry_base_seconds * (2**exponent))
