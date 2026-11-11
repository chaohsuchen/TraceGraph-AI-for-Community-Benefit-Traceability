# TraceGraph

TraceGraph turns a natural-language question about a public commitment into a graph of the organization, the commitment, and its source-backed findings. The MVP uses **hybrid catalog search**, **prompt-based LLM research and verification**, and an **interactive evidence graph**.

## How the MVP works

```mermaid
flowchart LR
    Q["User question"] --> S["BM25 and pgvector search"]
    S --> R["Fuse rankings and keep up to 5 candidates"]
    R --> L["LLM identity check"]
    L -->|MATCH| G["Interactive evidence graph"]
    L -->|AMBIGUOUS| C["Continue search conversation"]
    L -->|NO_MATCH| W["Prompt-based web research"]
    W --> V["Verify citations and assess delivery"]
    V -->|Documented promise| D["Save in PostgreSQL"]
    V -->|Original promise unverified| C
    C -->|Add details or research a lead| Q
    C -->|Choose a saved commitment| G
    D --> G
    G -->|Research again| F["Research the saved commitment"]
    F -->|Merge findings and reassess| D
```

1. **Retrieve and identify.** TraceGraph computes a local 384-dimensional feature-hash vector for the query. PostgreSQL with pgvector ranks stored commitment embeddings by cosine distance. BM25 in Python ranks the same catalog by keyword relevance. Reciprocal Rank Fusion combines the rankings by commitment ID. The LLM sees the query and compact fields for up to five candidates and compares the organization, obligation, scope, beneficiary, and timing. Python checks that a returned match ID belongs to those candidates.
2. **Route the request.** A match opens the saved graph. An ambiguous query returns a focused clarification. A miss queues research. The browser checks the job status and opens the graph when it is ready.
3. **Research the public record.** The discovery prompt asks the selected LLM to find the original promise, prioritize primary sources, and search for actual outcomes within a six-search budget. It asks the model to stop when the evidence is sufficient, actionable leads run out, or the budget is exhausted. The OpenAI adapter checks quoted passages against the public documents. TraceGraph requires a cited original promise and checks again for a duplicate before creating a new commitment.
4. **Assess delivery.** The LLM breaks the commitment into independently verifiable conditions and provides supporting citation IDs and gaps. Python checks the references and exact excerpts, and requires verified support for every required condition before assigning `CORROBORATED`. For example, evidence that a payment was made can support partial delivery when it gives no basis for saying the payment met the deadline. An announcement, forecast, or generated graph edge cannot serve as proof of delivery.

When an identity is ambiguous or research leaves the original promise unverified, the same search panel offers relevant saved commitments and cited web leads when available. Select a saved commitment to open its graph, choose a web lead to research it, or add a detail to narrow the next search. The panel keeps the conversation open until you choose **Quit**.

PostgreSQL is the source of truth for commitment identity, embeddings, findings, source citations, and the current assessment. The API generates JSON for the browser. The LLM handles semantic judgments, and Python handles routing, validation, and persistence.

## Explore and refresh a graph

- **Click** an organization, commitment, finding, or edge to inspect it. A finding is a source-backed claim. Its source links and excerpts appear in the side panel. The commitment shows the current verdict, verification date, and assessment summary.
- **Double-click** a node to focus its branch. For a finding, the view keeps that finding and its downstream progress. Use **Back** to restore the previous view, or **Fit view** to fit the visible graph. The list below the graph also lets you select findings and relations.
- **Expand the organization** to load its other commitments by organization ID. Progress arrows connect documented steps or revisions. Dashed conflict edges connect incompatible findings and let you inspect both sides.
- **Research again** rechecks the same commitment ID. The system merges duplicate findings, preserves distinct excerpts and their sources, and can run one targeted follow-up for an eligible unresolved conflict. It updates the current assessment from the cumulative evidence. A failed refresh leaves the existing assessment available.

## Run locally

Use Python 3.12 or newer and Node.js 20 or newer. Run the commands from the repository root. The live app also needs Docker Compose and a configured model provider.

### Try the graph without a key

```powershell
npm ci
node frontend/dev.mjs
```

Open the [synthetic demo](http://127.0.0.1:4173/?demo=1) to explore the graph, inspect sources, and try a simulated refresh.

### Run live research

Install dependencies, start PostgreSQL, and prepare the application schema.

```powershell
python -m pip install -r requirements.txt
npm ci
docker compose up -d --wait postgres
python -m tracegraph.runtime prepare
```

Start the backend with one provider. Supply the path to your local key file, or use `--provider openai` or `--provider anthropic` if the corresponding environment variable is already configured. Keep keys out of Git.

```powershell
python -m tracegraph.runtime serve --openai-key-file ./openai-api-key.txt
```

For Anthropic, use `python -m tracegraph.runtime serve --anthropic-key-file ./claude_api.txt`. Then start the frontend in a separate terminal.

```powershell
$env:TRACEGRAPH_BACKEND = 'http://127.0.0.1:8000'
node frontend/dev.mjs
```

Open [TraceGraph](http://127.0.0.1:4173/). For a first search, try “Did NASA’s OSIRIS-REx mission return at least 60 grams of material from Bennu to Earth on September 24, 2023?”

The default schema is `tracegraph_live` in the local Compose database. `prepare` creates the schema, applies migrations, and configures embeddings. Use `TRACEGRAPH_DATABASE_URL` and `TRACEGRAPH_SCHEMA` to override the defaults.

## Golden Dataset evaluation

The [Golden Workbook](evaluation/Golden%20Dataset%20%285%29.xlsx) is an evaluation reference. The live catalog starts empty and grows through its own research. For each selected case, the evaluator submits only the commitment question to a fresh catalog. The reference evidence, sources, and human labels stay in the local scoring process.

```powershell
$report = Join-Path $env:TEMP 'tracegraph-evaluation.json'
python -m tracegraph.runtime evaluate --commitment-id TG-GOLD-H008 --commitment-id TG-GOLD-H010 --openai-key-file ./openai-api-key.txt --output $report
```

The report separates confirmed one-to-one matches, evidence that needs review, unmatched evidence, and failed research jobs. Confirmed recall is matched reference evidence divided by all reference evidence in scored cases. With no IDs supplied, the evaluator selects the first two completed detail tabs by ID. See `evaluate --help` for options, including a custom workbook path and reviewed matches.

## Repository layout

| Directory | Contents |
| --- | --- |
| [`tracegraph/`](tracegraph/) | Python API, retrieval and identity checks, LLM prompts, research, reconciliation, and evaluation code |
| [`frontend/`](frontend/) | Browser app, Cytoscape graph, development server, and synthetic demo |
| [`db/`](db/) | PostgreSQL migrations and pgvector schema |
| [`contracts/`](contracts/) | [Current OpenAPI contract](contracts/openapi.json) and [API notes](contracts/README.md) |
| [`evaluation/`](evaluation/) | Golden reference workbook |
| [`tests/`](tests/) | Unit, PostgreSQL integration, and browser end-to-end tests |
| [`docs/`](docs/) | [Original Nov. 7 design notes](docs/nov-07-mvp-notes.txt) and [legacy snapshot API guide](docs/legacy-data-api.md) |

## Tests

With the local PostgreSQL container running, use the following commands. The integration and browser tests use disposable database schemas. The standard browser run uses fixture responses, so it needs no API key.

```powershell
$env:TEST_DATABASE_URL = 'postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph'
python -B -m unittest discover -s tests -v
npm test
npx playwright install chromium
python -B -m tests.e2e_runner
```

You can set `TRACEGRAPH_CHROME` to an installed Chrome executable instead of downloading Playwright’s browser.
