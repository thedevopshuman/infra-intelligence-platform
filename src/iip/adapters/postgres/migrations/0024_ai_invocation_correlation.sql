CREATE INDEX ai_usage_records_invocation_correlation_idx
    ON iip.ai_usage_records (
        tenant_id,
        (document->'spec'->'invocation'->>'traceId'),
        (document->'spec'->'invocation'->>'spanId')
    );
