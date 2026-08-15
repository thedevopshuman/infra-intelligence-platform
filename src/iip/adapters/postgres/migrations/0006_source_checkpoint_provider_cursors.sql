ALTER TABLE iip.source_checkpoints
    ADD COLUMN provider_cursors jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT source_checkpoints_provider_cursors_object
        CHECK (jsonb_typeof(provider_cursors) = 'object');
