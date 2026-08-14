CREATE TABLE iip.resource_projections (
    tenant_id text NOT NULL,
    resource_uid text NOT NULL,
    provider text NOT NULL,
    resource_type text NOT NULL,
    external_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    lifecycle text NOT NULL,
    observation_source_id text,
    observation_stream_id text,
    observation_sequence bigint,
    document_hash character(64) NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, resource_uid),
    UNIQUE (tenant_id, provider, resource_type, external_id),
    CHECK (resource_uid ~ '^res_[a-f0-9]{32}$'),
    CHECK (observation_sequence IS NULL OR observation_sequence >= 0)
);

CREATE TABLE iip.resource_observations (
    observation_offset bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    resource_uid text NOT NULL,
    observation_hash character(64) NOT NULL,
    disposition text NOT NULL CHECK (
        disposition IN ('accepted', 'stale', 'conflict')
    ),
    observed_at timestamptz NOT NULL,
    observation_source_id text,
    observation_stream_id text,
    observation_sequence bigint,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_id, resource_uid)
        REFERENCES iip.resource_projections (tenant_id, resource_uid),
    UNIQUE (tenant_id, resource_uid, observation_hash)
);

CREATE INDEX resource_observations_timeline_idx
    ON iip.resource_observations (tenant_id, resource_uid, observation_offset);

CREATE TABLE iip.event_log (
    event_offset bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    event_source text NOT NULL,
    event_id text NOT NULL,
    event_type text NOT NULL,
    subject text NOT NULL,
    event_time timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id, event_source, event_id),
    UNIQUE (tenant_id, event_offset)
);

CREATE INDEX event_log_replay_idx
    ON iip.event_log (tenant_id, event_offset);

CREATE TABLE iip.event_outbox (
    outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    event_offset bigint NOT NULL UNIQUE,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claimed_by text,
    claim_expires_at timestamptz,
    published_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_id, event_offset)
        REFERENCES iip.event_log (tenant_id, event_offset)
);

CREATE INDEX event_outbox_available_idx
    ON iip.event_outbox (tenant_id, available_at, outbox_id)
    WHERE published_at IS NULL;

CREATE TABLE iip.source_checkpoints (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    stream_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    checkpoint text NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, source_id)
);
