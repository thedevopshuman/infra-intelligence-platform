ALTER TABLE iip.evidence_artifacts
    ALTER COLUMN artifact DROP NOT NULL,
    ADD COLUMN artifact_deleted_at timestamptz,
    ADD CONSTRAINT evidence_artifacts_content_lifecycle_check CHECK (
        (artifact IS NOT NULL AND artifact_deleted_at IS NULL)
        OR
        (artifact IS NULL AND artifact_deleted_at IS NOT NULL)
    );

CREATE INDEX evidence_artifacts_retention_idx
    ON iip.evidence_artifacts (
        tenant_id,
        ((document->'spec'->'handling'->>'retentionClass')),
        recorded_at,
        evidence_id
    )
    WHERE artifact IS NOT NULL;
