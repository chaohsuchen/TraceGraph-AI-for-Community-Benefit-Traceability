BEGIN;

ALTER TABLE research_job ADD COLUMN claim_expires_at timestamptz;

-- Jobs left running by a process using the earlier schema can be reclaimed.
UPDATE research_job SET claim_expires_at = clock_timestamp() WHERE status = 'RUNNING';

ALTER TABLE research_job ADD CONSTRAINT research_job_running_lease
    CHECK ((status = 'RUNNING') = (claim_expires_at IS NOT NULL));

CREATE INDEX research_job_expired_claim_idx
    ON research_job (claim_expires_at) WHERE status = 'RUNNING';

COMMIT;
