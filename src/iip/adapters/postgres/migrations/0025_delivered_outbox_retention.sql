-- Only durable terminal acknowledgements are eligible for bounded retention.
-- The authoritative event log, event identity uniqueness, and quarantine rows
-- remain untouched. created_at is also tested against the retention cutoff.
CREATE INDEX event_outbox_published_retention_idx
    ON iip.event_outbox (tenant_id, published_at, outbox_id)
    INCLUDE (created_at)
    WHERE published_at IS NOT NULL
      AND quarantined_at IS NULL
      AND claimed_by IS NULL
      AND claim_expires_at IS NULL;
