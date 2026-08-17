CREATE TABLE iip.telemetry_export_health (
    instance_id text PRIMARY KEY CHECK (instance_id ~ '^sha256:[a-f0-9]{64}$'),
    component text NOT NULL CHECK (component IN ('api', 'workflow-worker')),
    started_at timestamptz NOT NULL,
    last_reported_at timestamptz NOT NULL,
    signals jsonb NOT NULL,
    CHECK (started_at <= last_reported_at),
    CHECK (jsonb_typeof(signals) = 'array')
);

CREATE INDEX telemetry_export_health_reported_idx
    ON iip.telemetry_export_health (last_reported_at, component, instance_id);
