CREATE TABLE iip.ai_savings_findings (
    tenant_id text NOT NULL,
    finding_id text NOT NULL,
    document_hash character(64) NOT NULL,
    rule_id text NOT NULL,
    rule_version text NOT NULL,
    severity text NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    region text NOT NULL,
    service_name text NOT NULL,
    deployment_environment text NOT NULL,
    baseline_start timestamptz NOT NULL,
    baseline_end timestamptz NOT NULL,
    current_start timestamptz NOT NULL,
    current_end timestamptz NOT NULL,
    evaluated_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, finding_id),
    CHECK (finding_id ~ '^aif_[a-f0-9]{32}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (rule_id = 'context-growth'),
    CHECK (rule_version = '1.0.0'),
    CHECK (severity IN ('info', 'low', 'medium', 'high')),
    CHECK (baseline_start < baseline_end),
    CHECK (baseline_end = current_start),
    CHECK (current_start < current_end),
    CHECK (baseline_end - baseline_start = current_end - current_start),
    CHECK (evaluated_at >= current_end)
);

CREATE INDEX ai_savings_findings_scope_time_idx
    ON iip.ai_savings_findings (
        tenant_id, provider, model_id, region, service_name,
        deployment_environment, current_end DESC, finding_id
    );

CREATE INDEX ai_savings_findings_severity_time_idx
    ON iip.ai_savings_findings (
        tenant_id, severity, evaluated_at DESC, finding_id
    );
