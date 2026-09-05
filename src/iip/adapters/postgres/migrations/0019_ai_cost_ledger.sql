CREATE TABLE iip.ai_price_catalogs (
    tenant_id text NOT NULL,
    catalog_id text NOT NULL,
    catalog_version text NOT NULL,
    document_hash character(64) NOT NULL,
    source_hash text NOT NULL,
    published_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, catalog_id),
    UNIQUE (tenant_id, catalog_version),
    UNIQUE (tenant_id, catalog_id, catalog_version, source_hash),
    CHECK (catalog_id ~ '^apc_[a-f0-9]{32}$'),
    CHECK (catalog_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[.][0-9]+$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (source_hash ~ '^sha256:[a-f0-9]{64}$')
);

CREATE TABLE iip.ai_cost_records (
    tenant_id text NOT NULL,
    cost_record_id text NOT NULL,
    usage_record_id text NOT NULL,
    catalog_id text NOT NULL,
    catalog_version text NOT NULL,
    catalog_source_hash text NOT NULL,
    engine_version text NOT NULL,
    document_hash character(64) NOT NULL,
    cost_status text NOT NULL,
    currency text,
    currency_scale smallint,
    total_subunits bigint,
    calculated_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, cost_record_id),
    UNIQUE (tenant_id, usage_record_id, catalog_id, engine_version),
    FOREIGN KEY (tenant_id, usage_record_id)
        REFERENCES iip.ai_usage_records (tenant_id, usage_record_id),
    FOREIGN KEY (
        tenant_id, catalog_id, catalog_version, catalog_source_hash
    ) REFERENCES iip.ai_price_catalogs (
        tenant_id, catalog_id, catalog_version, source_hash
    ),
    CHECK (cost_record_id ~ '^aic_[a-f0-9]{32}$'),
    CHECK (engine_version ~ '^[0-9]+[.][0-9]+[.][0-9]+$'),
    CHECK (catalog_source_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    CHECK (cost_status IN ('priced', 'unpriced', 'ambiguous')),
    CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CHECK (currency_scale IS NULL OR currency_scale IN (6, 9, 12)),
    CHECK (total_subunits IS NULL OR total_subunits >= 0),
    CHECK (
        (cost_status = 'priced' AND currency IS NOT NULL
            AND currency_scale IS NOT NULL AND total_subunits IS NOT NULL)
        OR
        (cost_status <> 'priced' AND currency IS NULL
            AND currency_scale IS NULL AND total_subunits IS NULL)
    )
);

CREATE INDEX ai_cost_records_usage_time_idx
    ON iip.ai_cost_records (tenant_id, calculated_at, cost_record_id);

CREATE INDEX ai_cost_records_status_time_idx
    ON iip.ai_cost_records (tenant_id, cost_status, calculated_at, cost_record_id);
