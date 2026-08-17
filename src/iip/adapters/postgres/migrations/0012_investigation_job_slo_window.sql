CREATE INDEX investigation_jobs_slo_window_idx
    ON iip.investigation_jobs (tenant_id, created_at, investigation_id)
    INCLUDE (state, updated_at);
