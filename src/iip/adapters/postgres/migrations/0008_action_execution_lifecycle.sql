CREATE TABLE iip.action_executions (
    tenant_id text NOT NULL,
    proposal_id text NOT NULL CHECK (proposal_id ~ '^act_[a-f0-9]{32}$'),
    state text NOT NULL CHECK (
        state IN (
            'executing', 'dry-run', 'succeeded', 'failed', 'rolled-back',
            'manual-reconciliation-required'
        )
    ),
    document jsonb NOT NULL,
    lease_expires_at timestamptz,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, proposal_id),
    FOREIGN KEY (tenant_id, proposal_id)
        REFERENCES iip.action_proposals (tenant_id, proposal_id),
    CHECK (
        (state = 'executing' AND lease_expires_at IS NOT NULL)
        OR (state <> 'executing' AND lease_expires_at IS NULL)
    )
);

CREATE INDEX action_executions_active_lease_idx
    ON iip.action_executions (lease_expires_at)
    WHERE state = 'executing';
