ALTER TABLE iip.investigations
    ALTER COLUMN report_document DROP NOT NULL,
    ADD COLUMN state text,
    ADD COLUMN status_document jsonb,
    ADD COLUMN lease_expires_at timestamptz;

UPDATE iip.investigations
SET state = CASE report_document->'spec'->>'outcome'
        WHEN 'failed' THEN 'failed'
        WHEN 'cancelled' THEN 'cancelled'
        ELSE 'completed'
    END,
    status_document = jsonb_build_object(
        'apiVersion', 'iip.platform/v1alpha1',
        'kind', 'InvestigationStatus',
        'metadata', jsonb_build_object(
            'id', investigation_id,
            'tenantId', tenant_id,
            'updatedAt', report_document->'metadata'->>'createdAt'
        ),
        'spec', jsonb_build_object(
            'requestDigest', request_digest,
            'state', CASE report_document->'spec'->>'outcome'
                WHEN 'failed' THEN 'failed'
                WHEN 'cancelled' THEN 'cancelled'
                ELSE 'completed'
            END,
            'startedAt', report_document->'spec'->>'startedAt',
            'completedAt', report_document->'spec'->>'completedAt',
            'reportRef', 'investigation://' || tenant_id || '/' || investigation_id || '/report'
        )
    );

ALTER TABLE iip.investigations
    ALTER COLUMN state SET NOT NULL,
    ALTER COLUMN status_document SET NOT NULL,
    ADD CONSTRAINT investigations_state_check
        CHECK (state IN ('running', 'cancellation-requested', 'completed', 'failed', 'cancelled')),
    ADD CONSTRAINT investigations_lifecycle_check
        CHECK (
            (state IN ('running', 'cancellation-requested') AND report_document IS NULL AND lease_expires_at IS NOT NULL)
            OR
            (state IN ('completed', 'failed', 'cancelled') AND report_document IS NOT NULL AND lease_expires_at IS NULL)
        );

CREATE INDEX investigations_running_lease_idx
    ON iip.investigations (lease_expires_at)
    WHERE state IN ('running', 'cancellation-requested');
