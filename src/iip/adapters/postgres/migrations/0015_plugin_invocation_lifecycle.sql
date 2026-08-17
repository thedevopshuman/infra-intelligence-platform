ALTER TABLE iip.plugin_invocations
    ADD COLUMN status_document jsonb,
    ADD COLUMN cancellation_document jsonb;

UPDATE iip.plugin_invocations AS invocation
SET status_document = jsonb_build_object(
    'apiVersion', 'iip.platform/v1alpha1',
    'kind', 'PluginInvocationStatus',
    'metadata', jsonb_build_object(
        'id', invocation.request_id,
        'sessionId', invocation.session_id,
        'tenantId', invocation.tenant_id,
        'pluginId', session.document->'metadata'->>'pluginId',
        'pluginVersion', session.document->'metadata'->>'pluginVersion',
        'updatedAt', to_jsonb(COALESCE(invocation.completed_at, invocation.claimed_at))
    ),
    'spec', jsonb_strip_nulls(jsonb_build_object(
        'requestDigest', invocation.request_digest,
        'state', CASE
            WHEN invocation.state = 'completed'
                THEN invocation.result_document->'spec'->>'status'
            ELSE 'claimed'
        END,
        'claimedAt', to_jsonb(invocation.claimed_at),
        'deadline', invocation.invocation_document->'metadata'->'deadline',
        'completedAt', to_jsonb(invocation.completed_at),
        'resultRef', CASE
            WHEN invocation.state = 'completed' THEN
                'plugin-result://' || invocation.tenant_id || '/sessions/' ||
                invocation.session_id || '/invocations/' || invocation.request_id
            ELSE NULL
        END
    ))
)
FROM iip.plugin_sessions AS session
WHERE session.tenant_id = invocation.tenant_id
  AND session.session_id = invocation.session_id;

ALTER TABLE iip.plugin_invocations
    ALTER COLUMN status_document SET NOT NULL,
    ADD CONSTRAINT plugin_invocations_cancellation_state_check CHECK (
        cancellation_document IS NULL
        OR status_document->'spec'->>'state' IN ('cancellation-requested', 'cancelled')
    );

CREATE INDEX plugin_invocations_lifecycle_idx
    ON iip.plugin_invocations (
        tenant_id,
        ((status_document->'spec'->>'state')),
        ((invocation_document->'metadata'->>'deadline'))
    );
