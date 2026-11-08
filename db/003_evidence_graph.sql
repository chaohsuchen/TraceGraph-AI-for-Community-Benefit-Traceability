BEGIN;

CREATE TABLE commitment_assessment (
    commitment_id text PRIMARY KEY REFERENCES commitment (id),
    revision bigint NOT NULL CHECK (revision > 0),
    label text NOT NULL CHECK (length(btrim(label)) > 0),
    verdict text NOT NULL CHECK (length(btrim(verdict)) > 0),
    reasoning text NOT NULL CHECK (length(btrim(reasoning)) > 0),
    verification_date date,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE evidence_finding (
    commitment_id text NOT NULL REFERENCES commitment (id),
    id text NOT NULL CHECK (length(btrim(id)) > 0),
    claim text NOT NULL CHECK (length(btrim(claim)) > 0),
    claim_key bytea NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (commitment_id, id),
    UNIQUE (commitment_id, claim_key)
);

CREATE TABLE evidence_support (
    id text PRIMARY KEY,
    commitment_id text NOT NULL,
    finding_id text NOT NULL,
    source_id text NOT NULL REFERENCES source (id),
    citation_key bytea NOT NULL,
    status text,
    explanation text,
    excerpt text,
    summary text,
    locator text,
    retrieved_at timestamptz,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (commitment_id, finding_id, source_id, citation_key),
    FOREIGN KEY (commitment_id, finding_id) REFERENCES evidence_finding (commitment_id, id)
);

CREATE TABLE evidence_relation (
    commitment_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PROGRESS', 'CONFLICT')),
    from_id text NOT NULL,
    to_id text NOT NULL,
    reasoning text NOT NULL CHECK (length(btrim(reasoning)) > 0),
    status text,
    preferred_id text,
    PRIMARY KEY (commitment_id, kind, from_id, to_id),
    FOREIGN KEY (commitment_id, from_id) REFERENCES evidence_finding (commitment_id, id),
    FOREIGN KEY (commitment_id, to_id) REFERENCES evidence_finding (commitment_id, id),
    CHECK (from_id <> to_id),
    CHECK (
        (kind = 'PROGRESS' AND status IS NULL AND preferred_id IS NULL)
        OR (
            kind = 'CONFLICT' AND status IS NOT NULL AND (
                (status = 'UNRESOLVED' AND preferred_id IS NULL)
                OR (status = 'RESOLVED' AND preferred_id IS NOT NULL AND preferred_id IN (from_id, to_id))
            )
        )
    )
);

CREATE UNIQUE INDEX evidence_relation_conflict_pair_idx
    ON evidence_relation (commitment_id, LEAST(from_id, to_id), GREATEST(from_id, to_id))
    WHERE kind = 'CONFLICT';

COMMIT;
