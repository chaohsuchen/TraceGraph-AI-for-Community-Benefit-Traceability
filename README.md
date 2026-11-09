# TraceGraph

TraceGraph finds public commitments and maintains an evidence graph in PostgreSQL with pgvector. It uses BM25 and vector retrieval to identify known commitments. A new commitment is created only after web research finds a cited original promise.

## Run locally

Use Python 3.12 or newer, Node.js 20 or newer, and Docker Compose. From the repository root, prepare the application database and install the graph dependency.

    python -m pip install -r requirements.txt
    npm ci
    docker compose up -d --wait postgres
    python -m tracegraph.runtime prepare

The default application schema is tracegraph_live in the local Compose database. Prepare creates the schema and configures embeddings. It does not load the Golden Workbook. The earlier workbook-backed database is preserved in its previous schema. The live runtime rejects a schema containing imported workbook annotations. TRACEGRAPH_DATABASE_URL and TRACEGRAPH_SCHEMA override the local defaults.

Start the API with ANTHROPIC_API_KEY or a local credential file.

    python -m tracegraph.runtime serve --anthropic-key-file ./claude_api.txt

For OpenAI, pass the local key file. If you already set `OPENAI_API_KEY`, use `serve --provider openai`. The OpenAI path uses `gpt-5.4-mini` by default and checks proposed excerpts against the cited public documents.

    python -m tracegraph.runtime serve --openai-key-file ./openai-api-key.txt

Run the frontend in another PowerShell window.

    $env:TRACEGRAPH_BACKEND = 'http://127.0.0.1:8000'
    node frontend/dev.mjs

Open [TraceGraph locally](http://127.0.0.1:4173/) or [the synthetic graph demo](http://127.0.0.1:4173/?demo=1). Use the graph to select a finding, compare conflicting accounts, and inspect citations.

## Research and refresh

Live discovery uses the Prompt-only policy. Claude breaks the original promise into verifiable criteria, checks each criterion against cited evidence, and pursues concrete gaps within a six-search budget.

A refresh reuses the commitment ID. The agent merges repeated factual findings, retains distinct source excerpts, adds directed progress edges and keeps both sides of a conflict. A newly identified conflict may trigger one targeted follow-up search. Adjudication requires cited evidence and keeps a reason for preferring one side. The current assessment uses all consolidated findings. Refresh jobs are tracked for retries and failures. A completed refresh does not create a new research snapshot in the v2 application schema.

Live model calls send the user query or public commitment context and, for reconciliation, the system's cited findings to the selected model provider. The app never reads Golden evidence or verdicts.

## Small evaluation

Run a small sample in fresh, separate database schemas. Only the selected natural-language commitment descriptions enter the research pipeline. The evaluator reads the workbook locally, then compares each returned evidence item against the corresponding authored reference rows.

    python -m tracegraph.runtime evaluate --commitment-id TG-GOLD-H008 --commitment-id TG-GOLD-H010 --anthropic-key-file ./claude_api.txt --output evaluation.json

With no IDs specified, the evaluator selects the first two completed detail tabs. The report separates confirmed one-to-one matches, cases for review and unmatched candidates. Confirmed recall divides matched reference rows by the number of reference rows in scorable cases. Failed provider requests are reported separately. Pass reviewed matches as a JSON object keyed by commitment ID. Each value is a list of objects with reference_row (the worksheet row number) and returned_index (one-based).

The [shared contract](./contracts/README.md) describes storage and graph shapes.

## Tests

Point TEST_DATABASE_URL at PostgreSQL with pgvector available. Tests use disposable schemas.

    $env:TEST_DATABASE_URL = 'postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph'
    python -B -m unittest discover -s tests -v
    npm test
    npx playwright install chromium
    python -B -m tests.e2e_runner

The browser suite starts an empty catalog and runs discovery, ID reuse, refresh, de-duplication and conflict inspection with synthetic provider responses. An optional public-only live browser case starts from an empty catalog and researches a public NASA commitment.

    python -B -m tests.e2e_runner --public-live --anthropic-key-file ./claude_api.txt
