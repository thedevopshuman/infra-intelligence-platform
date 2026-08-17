ALTER TABLE iip.event_outbox
    ADD COLUMN quarantined_at timestamptz;

ALTER TABLE iip.event_outbox
    ADD CONSTRAINT event_outbox_terminal_state_check CHECK (
        quarantined_at IS NULL
        OR (
            published_at IS NULL
            AND last_error_code IS NOT NULL
            AND claimed_by IS NULL
            AND claim_expires_at IS NULL
        )
    );

DROP INDEX iip.event_outbox_available_idx;

CREATE INDEX event_outbox_available_idx
    ON iip.event_outbox (tenant_id, available_at, outbox_id)
    WHERE published_at IS NULL AND quarantined_at IS NULL;

CREATE INDEX event_outbox_quarantine_idx
    ON iip.event_outbox (tenant_id, quarantined_at DESC, outbox_id DESC)
    WHERE quarantined_at IS NOT NULL;
