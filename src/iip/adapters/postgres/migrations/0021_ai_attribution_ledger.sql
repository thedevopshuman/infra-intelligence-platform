CREATE TABLE iip.ai_attribution_policies (
    tenant_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version text NOT NULL,
    document_hash character(64) NOT NULL,
    source_hash text NOT NULL,
    published_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, policy_id),
    UNIQUE (tenant_id, policy_version),
    UNIQUE (tenant_id, policy_id, policy_version, source_hash),
    CHECK (policy_id ~ '^aap_[a-f0-9]{32}$'),
    CHECK (policy_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (source_hash ~ '^sha256:[a-f0-9]{64}$')
);

CREATE TABLE iip.ai_usage_attributions (
    tenant_id text NOT NULL,
    attribution_record_id text NOT NULL,
    usage_record_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version text NOT NULL,
    policy_source_hash text NOT NULL,
    engine_version text NOT NULL,
    document_hash character(64) NOT NULL,
    status text NOT NULL,
    application_id text,
    team_id text,
    effective_at timestamptz NOT NULL,
    resolved_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, attribution_record_id),
    UNIQUE (tenant_id, usage_record_id, policy_id, engine_version),
    FOREIGN KEY (tenant_id, usage_record_id)
        REFERENCES iip.ai_usage_records (tenant_id, usage_record_id),
    FOREIGN KEY (tenant_id, policy_id, policy_version, policy_source_hash)
        REFERENCES iip.ai_attribution_policies (
            tenant_id, policy_id, policy_version, source_hash
        ),
    CHECK (attribution_record_id ~ '^aia_[a-f0-9]{32}$'),
    CHECK (engine_version ~ '^[0-9]+[.][0-9]+[.][0-9]+$'),
    CHECK (policy_source_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (status IN ('allocated', 'unallocated')),
    CHECK (
        (status = 'allocated' AND application_id IS NOT NULL AND team_id IS NOT NULL)
        OR
        (status = 'unallocated' AND application_id IS NULL AND team_id IS NULL)
    ),
    CHECK (resolved_at >= effective_at)
);

CREATE INDEX ai_usage_attributions_policy_time_idx
    ON iip.ai_usage_attributions (
        tenant_id, policy_id, effective_at, attribution_record_id
    );

CREATE INDEX ai_usage_attributions_allocation_time_idx
    ON iip.ai_usage_attributions (
        tenant_id, status, application_id, team_id,
        effective_at, attribution_record_id
    );
