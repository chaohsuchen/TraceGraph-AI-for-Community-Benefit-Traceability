# TraceGraph Data/API development

The local fixture API requires Python 3.12 and uses the standard library. Start it from the repository root.

```powershell
python -m tracegraph.api --port 8000 --cors-origin http://localhost:5173
python -B -m unittest discover -s tests -p test_data_api.py -v
```

Configure the frontend API base as `http://127.0.0.1:8000/api/v1`. Try a lookup query from [the shared examples](./contracts/examples.json). H008 and H010 share a source and present different evidence. Refresh H008 to get a new run and open the original run by ID. H001 has no completed result. The fixture worker returns `NO_PUBLIC_PROMISE` for misses and uses synthetic verification data for refreshes. The fixture API resets on restart.

For assembly with Retrieval and Agent, use `Application(store, agent, (ResolutionUnavailable,))`. The store supplies the snapshot methods and ResearchStore ports from the contract. `MemoryStore` can accept seeded graph and group fixtures and a mapping of commitment IDs to matching embeddings. The fixture server injects its own Agent. A real Agent, embedding model, and evidence provider are supplied during integration.

## PostgreSQL and workbook staging

Apply the migrations to a PostgreSQL database with pgvector installed.

```powershell
psql -v ON_ERROR_STOP=1 -f db/001_initial.sql
psql -v ON_ERROR_STOP=1 -f db/002_research_job_leases.sql
```

Configure the singleton `tracegraph_catalog` row with the selected embedding model and its dimensions before inserting searchable commitments. Each searchable commitment requires a nonzero vector. Cosine search uses the pgvector `<=>` operator. Acquire the catalog row lock before writing identities, compare its revision for discovery, and commit the new commitment, sources, evidence, run, and job terminal status together. Read catalog revision, scan, and vector ranking within a repeatable-read snapshot. Claim queued jobs with a row lock and enforce the claim token in terminal writes. Read completed runs by `completed_at DESC NULLS LAST, id DESC`.

To inspect the workbook and generate data for staging, install `openpyxl` in the Python environment, then run:

```powershell
python -m tracegraph.workbook_stage "Golden Dataset (5).xlsx" workbook-stage.csv
```

The command emits an issue report and an Excel row-preserving CSV. Use psql in the same directory as the CSV to stage it idempotently:

```sql
BEGIN;
CREATE TEMP TABLE workbook_load (LIKE workbook_annotation);
\copy workbook_load (workbook_id, sheet_name, row_number, gold_id, attributes) FROM 'workbook-stage.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
INSERT INTO workbook_annotation
SELECT * FROM workbook_load
ON CONFLICT (workbook_id, sheet_name, row_number) DO NOTHING;
COMMIT;
```

The worksheet name, physical row and column locations, hyperlinks, and workbook digest preserve provenance and separate duplicate ID labels. The issue report includes the conflicting A019 rows, A015 without a gold row, and the two `Sheet4` headers. Staging leaves `searched_on` as an original workbook field. The staging export keeps raw annotations separate from reviewed commitments and their embeddings.

## Durable API storage

Install psycopg 3 with `python -m pip install "psycopg[binary]>=3.2,<4"`. Apply both SQL migrations in numerical order. Use `PostgresStore(dsn)` in place of `MemoryStore` when wiring `Application` and the Agent. Its catalog snapshot implements both commitment scan and pgvector cosine ranking. The DSN should come from runtime configuration.

The API stores accepted requests and new jobs in one transaction. A worker claims jobs with a lease and renews it while executing. Another worker can reclaim expired jobs. Terminal writes check the lease token and job ID, and the database allows at most one completed run per job. Import or new commitment transactions advance the catalog revision used for duplicate checking.

Set `TEST_DATABASE_URL` and run `python -B -m unittest discover -s tests -p test_postgres_store.py -v` for the PostgreSQL tests. The suite creates and drops its own schema and needs permission to create it. The fixture-only suite continues to run without psycopg or a database.
