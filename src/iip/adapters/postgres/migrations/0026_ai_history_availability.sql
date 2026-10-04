-- Value-minimized markers make known-retired history distinguishable from data
-- that was never observed. This migration deliberately adds no cleanup writer.
CREATE TABLE iip.ai_retired_invocation_markers (
    tenant_id text NOT NULL,
    usage_record_id text NOT NULL,
    deduplication_key text NOT NULL,
    correlation_digest text NOT NULL,
    document_hash character(64) NOT NULL,
    recorded_at timestamptz NOT NULL,
    invocation_started_at timestamptz NOT NULL,
    retired_at timestamptz NOT NULL,
    policy_digest text NOT NULL,
    audit_ref text NOT NULL,
    PRIMARY KEY (tenant_id, usage_record_id),
    UNIQUE (tenant_id, deduplication_key),
    CHECK (tenant_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$'),
    CHECK (usage_record_id ~ '^aiu_[a-f0-9]{32}$'),
    CHECK (deduplication_key ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (correlation_digest ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (policy_digest ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (
        audit_ref ~ '^audit://[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/[a-zA-Z0-9._/-]{1,255}$'
    ),
    CHECK (
        left(audit_ref, length('audit://' || tenant_id || '/'))
            = 'audit://' || tenant_id || '/'
    ),
    CHECK (invocation_started_at <= recorded_at),
    CHECK (recorded_at <= retired_at)
);

CREATE INDEX ai_retired_invocations_time_idx
    ON iip.ai_retired_invocation_markers (
        tenant_id, invocation_started_at, usage_record_id
    );

-- Correlation digests are intentionally non-unique: multiple usage records can
-- legitimately share one trace/span correlation across telemetry channels.
CREATE INDEX ai_retired_invocations_correlation_idx
    ON iip.ai_retired_invocation_markers (tenant_id, correlation_digest);
