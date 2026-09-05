CREATE TABLE iip.ai_model_suitability_reports (
    tenant_id text NOT NULL,
    report_id text NOT NULL,
    document_hash character(64) NOT NULL,
    source_kind text NOT NULL,
    source_hash text NOT NULL,
    provider text NOT NULL,
    reference_model_id text NOT NULL,
    candidate_model_id text NOT NULL,
    region text NOT NULL,
    service_name text NOT NULL,
    deployment_environment text NOT NULL,
    workload_profile_id text NOT NULL,
    evaluated_at timestamptz NOT NULL,
    valid_until timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, report_id),
    CHECK (report_id ~ '^ams_[a-f0-9]{32}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (source_kind IN ('operator-attested', 'test-fixture')),
    CHECK (source_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (reference_model_id <> candidate_model_id),
    CHECK (evaluated_at < valid_until),
    CHECK (valid_until - evaluated_at <= interval '90 days')
);

CREATE INDEX ai_model_suitability_scope_time_idx
    ON iip.ai_model_suitability_reports (
        tenant_id, provider, reference_model_id, candidate_model_id,
        region, service_name, deployment_environment, valid_until DESC,
        report_id
    );

ALTER TABLE iip.ai_savings_findings
    DROP CONSTRAINT ai_savings_findings_rule_id_check,
    DROP CONSTRAINT ai_savings_findings_rule_version_check;

ALTER TABLE iip.ai_savings_findings
    ADD CONSTRAINT ai_savings_findings_rule_id_check
        CHECK (rule_id IN (
            'context-growth',
            'retry-amplification',
            'expensive-model-anomaly'
        )),
    ADD CONSTRAINT ai_savings_findings_rule_version_check
        CHECK (
            (rule_id = 'context-growth' AND rule_version = '1.0.0')
            OR
            (rule_id = 'retry-amplification' AND rule_version = '1.0.0')
            OR
            (rule_id = 'expensive-model-anomaly' AND rule_version = '1.0.0')
        );
