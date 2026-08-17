CREATE TABLE iip.plugin_invocations (
    tenant_id text NOT NULL,
    request_id text NOT NULL CHECK (request_id ~ '^pin_[a-f0-9]{32}$'),
    session_id text NOT NULL CHECK (session_id ~ '^psn_[a-f0-9]{32}$'),
    request_digest text NOT NULL CHECK (request_digest ~ '^sha256:[a-f0-9]{64}$'),
    invocation_document jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('claimed', 'completed')),
    result_document jsonb,
    claimed_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, request_id),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES iip.plugin_sessions (tenant_id, session_id),
    CHECK (
        (state = 'claimed' AND result_document IS NULL AND completed_at IS NULL)
        OR
        (state = 'completed' AND result_document IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX plugin_invocations_session_idx
    ON iip.plugin_invocations (tenant_id, session_id, claimed_at, request_id);
