BEGIN;

CREATE TABLE catalog_pdf (
    sha256 text PRIMARY KEY,
    source_id text NOT NULL UNIQUE REFERENCES source(id),
    document_id text NOT NULL,
    content bytea NOT NULL
);

CREATE TABLE catalog_pdf_citation (
    commitment_id text NOT NULL REFERENCES commitment(id),
    pdf_hash text NOT NULL REFERENCES catalog_pdf(sha256),
    page integer NOT NULL CHECK (page > 0),
    char_start integer NOT NULL CHECK (char_start >= 0),
    char_end integer NOT NULL CHECK (char_end > char_start),
    excerpt text NOT NULL,
    PRIMARY KEY (commitment_id, pdf_hash, char_start, char_end)
);

CREATE TABLE catalog_pdf_item (
    item_key text PRIMARY KEY,
    commitment_id text NOT NULL REFERENCES commitment(id)
);
COMMIT;
