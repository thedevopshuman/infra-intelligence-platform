CREATE TABLE iip.investigation_jobs (
    tenant_id text NOT NULL,
    investigation_id text NOT NULL,
    actor_id text NOT NULL,
    actor_roles jsonb NOT NULL DEFAULT '[]'::jsonb,
    request_digest text NOT NULL,
    request_document jsonb NOT NULL,
    status_document jsonb NOT NULL,
    state text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz,
    lease_owner text,
    claim_token text,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, investigation_id),
    CHECK (state IN ('queued', 'running', 'cancellation-requested', 'completed', 'failed', 'cancelled')),
    CHECK (attempts >= 0 AND attempts <= 1000),
    CHECK (
        (state = 'queued' AND available_at IS NOT NULL AND lease_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL)
        OR
        (state IN ('running', 'cancellation-requested') AND available_at IS NULL AND lease_owner IS NOT NULL AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (state IN ('completed', 'failed', 'cancelled') AND available_at IS NULL AND lease_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL)
    )
);

CREATE INDEX investigation_jobs_ready_idx
    ON iip.investigation_jobs (tenant_id, available_at, created_at, investigation_id)
    WHERE state = 'queued';

CREATE INDEX investigation_jobs_abandoned_idx
    ON iip.investigation_jobs (tenant_id, lease_expires_at, created_at, investigation_id)
    WHERE state IN ('running', 'cancellation-requested');
