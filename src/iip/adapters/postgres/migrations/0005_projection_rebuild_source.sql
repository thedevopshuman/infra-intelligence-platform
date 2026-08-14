-- Immutable observations must survive loss or replacement of serving projections.
ALTER TABLE iip.resource_observations
    DROP CONSTRAINT resource_observations_tenant_id_resource_uid_fkey;
