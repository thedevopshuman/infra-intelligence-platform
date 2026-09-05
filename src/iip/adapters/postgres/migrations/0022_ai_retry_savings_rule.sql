ALTER TABLE iip.ai_savings_findings
    DROP CONSTRAINT ai_savings_findings_rule_id_check,
    DROP CONSTRAINT ai_savings_findings_rule_version_check;

ALTER TABLE iip.ai_savings_findings
    ADD CONSTRAINT ai_savings_findings_rule_id_check
        CHECK (rule_id IN ('context-growth', 'retry-amplification')),
    ADD CONSTRAINT ai_savings_findings_rule_version_check
        CHECK (
            (rule_id = 'context-growth' AND rule_version = '1.0.0')
            OR
            (rule_id = 'retry-amplification' AND rule_version = '1.0.0')
        );
