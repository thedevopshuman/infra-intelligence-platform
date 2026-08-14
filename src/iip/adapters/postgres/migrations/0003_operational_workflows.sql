CREATE TABLE iip.evidence_artifacts (
    tenant_id text NOT NULL,
    evidence_id text NOT NULL CHECK (evidence_id ~ '^evd_[a-f0-9]{32}$'),
    document jsonb NOT NULL,
    artifact bytea NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^sha256:[a-f0-9]{64}$'),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, evidence_id)
);

CREATE INDEX evidence_artifacts_recorded_idx
    ON iip.evidence_artifacts (tenant_id, recorded_at DESC, evidence_id);

CREATE TABLE iip.investigations (
    tenant_id text NOT NULL,
    investigation_id text NOT NULL CHECK (investigation_id ~ '^inv_[a-f0-9]{32}$'),
    request_digest text NOT NULL CHECK (request_digest ~ '^sha256:[a-f0-9]{64}$'),
    request_document jsonb NOT NULL,
    report_document jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, investigation_id)
);

CREATE TABLE iip.action_proposals (
    tenant_id text NOT NULL,
    proposal_id text NOT NULL CHECK (proposal_id ~ '^act_[a-f0-9]{32}$'),
    idempotency_key text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, proposal_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE iip.action_approvals (
    tenant_id text NOT NULL,
    approval_id text NOT NULL CHECK (approval_id ~ '^apr_[a-f0-9]{32}$'),
    proposal_id text NOT NULL CHECK (proposal_id ~ '^act_[a-f0-9]{32}$'),
    document jsonb NOT NULL,
    decided_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, approval_id),
    UNIQUE (tenant_id, proposal_id),
    FOREIGN KEY (tenant_id, proposal_id)
        REFERENCES iip.action_proposals (tenant_id, proposal_id)
);

CREATE TABLE iip.action_results (
    tenant_id text NOT NULL,
    proposal_id text NOT NULL CHECK (proposal_id ~ '^act_[a-f0-9]{32}$'),
    document jsonb NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, proposal_id),
    FOREIGN KEY (tenant_id, proposal_id)
        REFERENCES iip.action_proposals (tenant_id, proposal_id)
);

CREATE TABLE iip.plugin_sessions (
    tenant_id text NOT NULL,
    session_id text NOT NULL CHECK (session_id ~ '^psn_[a-f0-9]{32}$'),
    document jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, session_id)
);

CREATE TABLE iip.audit_records (
    audit_offset bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    category text NOT NULL,
    document jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX audit_records_tenant_offset_idx
    ON iip.audit_records (tenant_id, audit_offset);
