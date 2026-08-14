CREATE TABLE iip.resource_relationships (
    tenant_id text NOT NULL,
    edge_id text NOT NULL,
    observed_resource_uid text NOT NULL,
    relationship_type text NOT NULL,
    source_ref text NOT NULL,
    target_ref text NOT NULL,
    attributes jsonb NOT NULL CHECK (jsonb_typeof(attributes) = 'object'),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, edge_id),
    FOREIGN KEY (tenant_id, observed_resource_uid)
        REFERENCES iip.resource_projections (tenant_id, resource_uid)
        ON DELETE CASCADE,
    CHECK (edge_id ~ '^rel_[a-f0-9]{32}$'),
    CHECK (observed_resource_uid ~ '^res_[a-f0-9]{32}$'),
    CHECK (relationship_type ~ '^[a-z][a-z0-9._-]{0,63}$')
);

CREATE INDEX resource_relationships_source_idx
    ON iip.resource_relationships (tenant_id, source_ref, edge_id)
    WHERE source_ref ~ '^res_[a-f0-9]{32}$';

CREATE INDEX resource_relationships_target_idx
    ON iip.resource_relationships (tenant_id, target_ref, edge_id)
    WHERE target_ref ~ '^res_[a-f0-9]{32}$';
