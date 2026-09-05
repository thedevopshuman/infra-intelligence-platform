CREATE TABLE iip.ai_usage_records (
    tenant_id text NOT NULL,
    usage_record_id text NOT NULL,
    deduplication_key text NOT NULL,
    document_hash character(64) NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    service_name text NOT NULL,
    invocation_started_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, usage_record_id),
    UNIQUE (tenant_id, deduplication_key),
    CHECK (usage_record_id ~ '^aiu_[a-f0-9]{32}$'),
    CHECK (deduplication_key ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$')
);

CREATE INDEX ai_usage_records_time_idx
    ON iip.ai_usage_records (tenant_id, invocation_started_at, usage_record_id);

CREATE INDEX ai_usage_records_model_time_idx
    ON iip.ai_usage_records (
        tenant_id, provider, model_id, invocation_started_at, usage_record_id
    );

CREATE INDEX ai_usage_records_service_time_idx
    ON iip.ai_usage_records (
        tenant_id, service_name, invocation_started_at, usage_record_id
    );
