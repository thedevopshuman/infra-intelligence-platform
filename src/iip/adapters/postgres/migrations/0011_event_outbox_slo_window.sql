CREATE INDEX event_outbox_slo_window_idx
    ON iip.event_outbox (tenant_id, created_at, outbox_id)
    INCLUDE (published_at, quarantined_at);
