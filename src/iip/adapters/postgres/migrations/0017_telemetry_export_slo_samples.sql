CREATE TABLE iip.telemetry_export_health_samples (
    instance_id text NOT NULL CHECK (instance_id ~ '^sha256:[a-f0-9]{64}$'),
    component text NOT NULL CHECK (component IN ('api', 'workflow-worker')),
    started_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    signals jsonb NOT NULL,
    PRIMARY KEY (instance_id, observed_at),
    CHECK (started_at <= observed_at),
    CHECK (jsonb_typeof(signals) = 'array'),
    CHECK (jsonb_array_length(signals) = 2)
);

CREATE INDEX telemetry_export_health_samples_window_idx
    ON iip.telemetry_export_health_samples (observed_at, instance_id);
