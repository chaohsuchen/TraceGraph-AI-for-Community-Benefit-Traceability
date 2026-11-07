# TraceGraph

TraceGraph matches a natural-language query to a public commitment and presents its sources, evidence and research history as a graph. PostgreSQL with pgvector is the canonical store. The API and worker run in one local Python process, and the frontend uses the HTTP contract.

## Run locally

Use Python 3.12 or newer, Node.js 20 or newer, and Docker Compose. From the repository root:

    python -m pip install -r requirements.txt
    docker compose up -d --wait postgres
    python -m tracegraph.runtime prepare

The default database is the local Compose PostgreSQL instance. Set TRACEGRAPH_DATABASE_URL to use another PostgreSQL database with pgvector available.

Start the API with the Anthropic key supplied through ANTHROPIC_API_KEY or an explicit local key file. An untracked claude_api.txt file may contain the key on one line.

    python -m tracegraph.runtime serve --anthropic-key-file ./claude_api.txt

Run the frontend in another PowerShell window.

    $env:TRACEGRAPH_BACKEND = 'http://127.0.0.1:8000'
    node frontend/dev.mjs

Open [TraceGraph locally](http://127.0.0.1:4173/). [Fixture demo](http://127.0.0.1:4173/?demo=1) runs with browser fixtures.

The live backend uses Claude for identity decisions and web research. Set ANTHROPIC_MODEL to override the default model. The local embedding profile uses normalized, signed word and character features for reproducible vector ranking. PostgreSQL stores the vectors and computes cosine ranking, which the application fuses with BM25. Live lookup sends the user query and the compact shortlist to Anthropic. Live research sends the commitment being verified and receives web results and cited passages.

## Workbook and Golden Dataset

The importer preserves original worksheet rows and the existing gold IDs. The six authored commitment tabs supply historical runs and evidence. Missing verification and retrieval dates remain null. A019 keeps both conflicting annotations, A015 remains a staged research row, and Sheet4 remains staged review material. Re-running prepare with the same workbook preserves existing commitments and research runs.

Generate the evaluation export with original annotations and current database predictions joined by ID.

    python -m tracegraph.runtime export-golden --output golden-evaluation.json

The [shared contract](./contracts/README.md) describes the API and field boundaries.

## Tests

Point TEST_DATABASE_URL at the local test PostgreSQL database. Database tests create and remove an isolated schema.

    $env:TEST_DATABASE_URL = 'postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph'
    python -B -m unittest discover -s tests -v
    npm install
    npm test
    npx playwright install chromium
    python -B -m tests.e2e_runner

The browser suite launches the real frontend, backend, worker and PostgreSQL adapter. It imports the workbook into a temporary schema and uses fixed model and web responses. Run the live Claude lookup and web refresh separately with a key authorized for sending the test commitment data to Anthropic.

    python -B -m tests.e2e_runner --live --anthropic-key-file ./claude_api.txt
