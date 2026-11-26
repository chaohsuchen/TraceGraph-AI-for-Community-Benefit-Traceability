# TraceGraph

TraceGraph is an MScAC course project for tracing public commitments. Give it a natural-language question and it identifies the promise, researches what happened, and shows the verdict alongside the source passages that support it. You can also build a searchable catalog from trusted PDFs before researching delivery.

## Try the graph

From the repository root, install the frontend dependencies with Node.js 20 or newer, then start the demo.

```powershell
npm ci
npm start
```

Open the [interactive demo](http://127.0.0.1:4173/?demo=1). It uses synthetic data and runs without a model key or database. Click a finding to inspect its source and excerpt. Double-click a node to make it the root and show its downstream branch. **Back** restores the previous view. Select an organization to load its other commitments. Progress arrows link documented steps or revisions, and dashed conflict edges let you inspect both accounts.

## Run the live application

Use Python 3.12 or newer, Node.js 20 or newer, and Docker Compose. Run these commands in PowerShell from the repository root. Start the database and API in the first terminal.

```powershell
python -m pip install -r requirements.txt
npm ci
docker compose up -d --wait postgres
python -m tracegraph.runtime prepare
python -m tracegraph.runtime serve --openai-key-file ./openai-api-key.txt
```

In a second terminal, point the frontend at the API and start it.

```powershell
$env:TRACEGRAPH_BACKEND = 'http://127.0.0.1:8000'
npm start
```

Open the [live application](http://127.0.0.1:4173/). For example, search for *Did NASA’s OSIRIS-REx mission return at least 60 grams of material from Bennu on September 24, 2023?* A new catalog can research its first commitment from a question alone.

OpenAI is the default live provider. You can use `--provider openai` with `OPENAI_API_KEY`, or use `--anthropic-key-file ./claude_api.txt` for Anthropic. Keep key files local. The default PostgreSQL schema is `tracegraph_live`, and Compose exposes the database on localhost port 55432. Set `TRACEGRAPH_DATABASE_URL` and `TRACEGRAPH_SCHEMA` to select another catalog.

## How a question becomes an evidence graph

```mermaid
flowchart LR
    PDF[Trusted PDF] --> Extract[Extract and review] --> Import[Import and deduplicate] --> DB[(PostgreSQL)]
    Q[User question] --> Search[BM25 + pgvector] --> Identity[LLM identity check]
    DB --> Search
    Identity -->|Researched match| Graph[Interactive graph]
    Identity -->|Ambiguous| Clarify[Clarify or select] --> Q
    Identity -->|New or unresearched| Research[Research and verify]
    Research -->|Verified findings| DB
    DB --> Graph
    Graph -->|Research again| Research
```

| Stage | What TraceGraph does |
| --- | --- |
| Retrieve | BM25 ranks stored descriptions and fields by keyword relevance. PostgreSQL with pgvector ranks local 384-dimensional feature-hash embeddings by cosine distance. Reciprocal Rank Fusion combines both rankings by commitment ID. |
| Identify | The LLM compares the question with up to five candidates using the promising organization, obligation, scope, beneficiary and timing. Python validates the returned ID. A researched match opens its saved graph. A match to an unresearched PDF record starts its first research job. |
| Clarify or research | Ambiguous identity opens a conversation where the user can choose a saved record, investigate a cited lead, add detail or quit. A catalog miss starts prompt-based web research. The discovery prompt looks for the original promise and delivery evidence with a six-search budget. Creating a web-discovered record requires a cited original promise and a duplicate check. |
| Assess | The LLM breaks the obligation into verifiable conditions and evaluates what the passages establish. Python checks citation ownership, quoted text and structural consistency. Full corroboration requires verified delivery support for every required condition. The graph exposes the verdict, verification date, sources and remaining gaps. |
| Refresh | **Research again** keeps the commitment ID. It can follow references to new documents, deduplicates verified observations, checks conflicts and reassesses the accumulated evidence. If a refresh fails, the previous assessment remains available. |

PostgreSQL stores commitments, embeddings, findings, citations, research history and assessments. The API builds graph JSON from these relationships. The OpenAPI contract describes the lookup, conversation, graph, refresh and job endpoints. [API contract](contracts/openapi.json)

## Catalog commitments from trusted PDFs

PDF ingestion has two commands. **Extract** writes reviewed, source-anchored candidates to disk. **Import** writes commitments and citations to PostgreSQL. Use the same source PDFs in both steps so the importer can verify their hashes, pages, offsets and quoted text. Both steps use OpenAI for model judgments with web tools disabled.

**1. Extract.** Place your PDFs in a local `inputs` directory. The model reads each document with neighboring text, checks for omissions and duplicates, then makes a final delete-only pass over the candidates. The output is `pdf-run/reviewed.json`.

```powershell
$pdfs = (Get-ChildItem -File .\inputs\*.pdf).FullName
python -m tracegraph.pdf_catalog extract @pdfs --output ./pdf-run --openai-key-file ./openai-api-key.txt
```

**2. Import.** With PostgreSQL running, import the reviewed file. The report identifies new records, links to existing commitments, previously imported rows, and cases that still need disambiguation.

```powershell
python -m tracegraph.pdf_catalog import ./pdf-run/reviewed.json @pdfs --report ./pdf-run/import.json --openai-key-file ./openai-api-key.txt
```

New commitments start with research status `NOT_STARTED`. The first lookup queues delivery research and concurrent lookups share the same job. Importing an accepted row again reuses its record. Each PDF is stored once and the graph links back to its cited pages.

To use the course release for these same three PDFs, skip extraction and pass the frozen `reviewed.json` to the import command. Run it with the three original PDFs and an existing directory for the report. The export contains **156 source rows grouped into 151 commitments** in the frozen benchmark. The live importer resolves identity against the catalog it receives. [Frozen import file](evaluation/pdf-extraction-101/all_documents/single-pass/reviewed.json) · [PDF names and SHA-256 hashes](evaluation/pdf-extraction-101/manifest.json)

## Evaluation

**Commitment extraction.** The frozen release was evaluated on three development PDFs using one-to-one citation-span matching. To replay both scores locally, run `python -m evaluation.pdf-extraction-101.all_documents.release` after installing the Python dependencies. The replay needs no model key or database.

| Reference set | Matched / references | Outputs | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original Golden | 82 / 101 | 151 | 54.30% | 81.19% | 65.08% |
| Golden plus 10 source-checked additions | 91 / 111 | 151 | 60.26% | 81.98% | 69.47% |

The ten additions came from auditing unmatched TraceGraph predictions. All ten enter the expanded reference, including one that the final extractor dropped. Use the same reference set when comparing systems. [Extraction report and audited additions](evaluation/pdf-extraction-101/all_documents/README.md)

**Delivery evidence and verdicts.** The Golden workbook supplies evaluation references. Live records come from trusted PDF imports or system research. The workbook evaluator researches selected commitments in isolated catalogs and measures recovery of reference evidence. This example evaluates two cases.

```powershell
$report = Join-Path $env:TEMP 'tracegraph-evaluation.json'
python -m tracegraph.runtime evaluate --commitment-id TG-GOLD-H008 --commitment-id TG-GOLD-H010 --openai-key-file ./openai-api-key.txt --output $report
```

For a frozen research output, the tool-free LLM judge checks whether cited passages entail, contradict or leave each claim unresolved. It also grades whether the displayed verdict follows from the evidence. Python validates the judge’s IDs and quotes before scoring. [Judge implementation](tracegraph/nli_judge.py) · [Verdict evaluation](evaluation/verdict-grounding/report.md) · [Evidence coverage study](evaluation/coverage-loss/README.md)

## Tests

With the local Compose database running, use the commands below. Database tests create disposable schemas. Browser tests use a real backend and PostgreSQL with fixed research responses.

```powershell
$env:TEST_DATABASE_URL = 'postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph'
python -B -m unittest discover -s tests -v
npm test
npx playwright install chromium
python -B -m tests.e2e_runner
python -B -m tests.e2e_runner --pdf-catalog
```

Set `TRACEGRAPH_CHROME` to the path of an installed Chrome executable to use that browser.

## Code map

| Area | Start here |
| --- | --- |
| API, worker and database setup | [Runtime](tracegraph/runtime.py) · [Request handlers](tracegraph/api.py) · [Schema migrations](db/) |
| Catalog lookup and research | [Hybrid retrieval](tracegraph/retrieval.py) · [Agent routing](tracegraph/agent.py) · [Discovery prompt](tracegraph/experimental_prompt.py) |
| Evidence and verdicts | [Reconciliation](tracegraph/reconcile.py) · [Evidence storage](tracegraph/evidence_store.py) |
| PDF ingestion | [PDF commands](tracegraph/pdf_catalog.py) · [Frozen extraction workflow](evaluation/pdf-extraction-101/all_documents/extract.py) · [Database importer](tracegraph/pdf_import.py) |
| Interactive graph | [Frontend](frontend/) · [Graph interactions](frontend/graph-view.mjs) |
| Evaluation and design | [Evaluation artifacts](evaluation/) · [Tests](tests/) · [Nov. 7 MVP design notes](docs/nov-07-mvp-notes.txt) |
