BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tracegraph_catalog (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0,
    embedding_model text,
    embedding_dimensions integer CHECK (embedding_dimensions > 0),
    CHECK ((embedding_model IS NULL) = (embedding_dimensions IS NULL))
);

INSERT INTO tracegraph_catalog DEFAULT VALUES;

CREATE TABLE organization (
    id text PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0)
);

CREATE TABLE source (
    id text PRIMARY KEY,
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    url text,
    citation text,
    publisher text,
    source_type text,
    published_on date,
    CHECK (coalesce(length(btrim(url)), 0) > 0 OR coalesce(length(btrim(citation)), 0) > 0)
);

CREATE UNIQUE INDEX source_url_idx ON source (url) WHERE url IS NOT NULL;

CREATE TABLE commitment (
    id text PRIMARY KEY,
    organization_id text REFERENCES organization (id),
    title text,
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    original_text text,
    responsible_entity_raw text,
    beneficiary_category text,
    beneficiary_named text,
    obligation_type text,
    amount_value text,
    term_periodicity text,
    geographic_scope text,
    condition_contingency text,
    milestone text,
    notes text,
    original_document_id text,
    original_source_id text REFERENCES source (id),
    original_locator text,
    original_char_start integer CHECK (original_char_start >= 0),
    original_char_end integer CHECK (original_char_end >= 0),
    embedding vector NOT NULL,
    CHECK (original_char_end >= original_char_start)
);

CREATE INDEX commitment_organization_idx ON commitment (organization_id, id);

CREATE FUNCTION tracegraph_check_embedding()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_dimensions integer;
BEGIN
    SELECT embedding_dimensions INTO expected_dimensions FROM tracegraph_catalog WHERE singleton;
    IF expected_dimensions IS NULL OR vector_dims(NEW.embedding) <> expected_dimensions THEN
        RAISE EXCEPTION 'Embedding dimensions do not match the configured TraceGraph model';
    END IF;
    IF vector_norm(NEW.embedding) = 0 THEN
        RAISE EXCEPTION 'Commitment embeddings must have nonzero norm';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER commitment_check_embedding
BEFORE INSERT OR UPDATE OF embedding ON commitment
FOR EACH ROW EXECUTE FUNCTION tracegraph_check_embedding();

CREATE FUNCTION tracegraph_bump_catalog()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tracegraph_catalog SET revision = revision + 1 WHERE singleton;
    RETURN NULL;
END;
$$;

CREATE TRIGGER commitment_catalog_changed
AFTER INSERT OR UPDATE OR DELETE ON commitment
FOR EACH STATEMENT EXECUTE FUNCTION tracegraph_bump_catalog();

CREATE TRIGGER organization_catalog_changed
AFTER UPDATE ON organization
FOR EACH STATEMENT EXECUTE FUNCTION tracegraph_bump_catalog();

CREATE TABLE research_job (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('DISCOVER', 'REFRESH')),
    status text NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
    query text,
    commitment_id text REFERENCES commitment (id),
    claim_token text,
    result jsonb,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'DISCOVER' AND query IS NOT NULL AND commitment_id IS NULL)
        OR (kind = 'REFRESH' AND query IS NULL AND commitment_id IS NOT NULL)),
    CHECK ((status = 'RUNNING') = (claim_token IS NOT NULL)),
    CHECK ((status = 'COMPLETED') = (result IS NOT NULL)),
    CHECK ((status = 'FAILED') = (error IS NOT NULL))
);

CREATE INDEX research_job_queue_idx
    ON research_job (created_at, id) WHERE status = 'QUEUED';

CREATE TABLE research_run (
    id text PRIMARY KEY,
    commitment_id text NOT NULL REFERENCES commitment (id),
    job_id text UNIQUE REFERENCES research_job (id),
    verification_date date,
    verdict text NOT NULL CHECK (length(btrim(verdict)) > 0),
    reasoning text NOT NULL CHECK (length(btrim(reasoning)) > 0),
    completed_at timestamptz,
    CHECK (job_id IS NULL OR (verification_date IS NOT NULL AND completed_at IS NOT NULL))
);

CREATE INDEX research_run_latest_idx
    ON research_run (commitment_id, completed_at DESC NULLS LAST, id DESC);

CREATE TABLE evidence (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES research_run (id),
    source_id text NOT NULL REFERENCES source (id),
    status text,
    explanation text,
    excerpt text,
    summary text,
    locator text,
    retrieved_at timestamptz
);

CREATE INDEX evidence_run_source_idx ON evidence (run_id, source_id);

CREATE TABLE api_idempotency (
    scope text NOT NULL,
    key text NOT NULL,
    request jsonb NOT NULL,
    response_status integer NOT NULL CHECK (response_status IN (200, 202)),
    response_headers jsonb NOT NULL,
    response_body jsonb NOT NULL,
    job_id text REFERENCES research_job (id),
    PRIMARY KEY (scope, key),
    CHECK ((response_status = 202) = (job_id IS NOT NULL))
);

-- Preserve workbook annotations by sheet and row. A single gold ID can have
-- conflicting rows and some legacy records have no matching commitment.
CREATE TABLE workbook_annotation (
    workbook_id text NOT NULL,
    sheet_name text NOT NULL,
    row_number integer NOT NULL CHECK (row_number > 0),
    gold_id text,
    attributes jsonb NOT NULL,
    PRIMARY KEY (workbook_id, sheet_name, row_number)
);

CREATE INDEX workbook_annotation_gold_idx ON workbook_annotation (gold_id);

COMMIT;
