# Delete-only review across all PDF types

## Frozen release and expanded reference

The selected release uses one delete-only verification across all document types. Its import file contains 156 cited source rows grouped into 151 commitment IDs. The 158-record confirmation variant is a comparison. The release includes the [exact import file](single-pass/reviewed.json) and a [lock with hashes, reference-to-catalog matches and audited IDs](single-pass/release.lock.json).

| Extraction benchmark | Reference commitments | Predictions | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original Golden | 101 | 151 | 82 | 69 | 19 | 54.30% | 81.19% | 65.08% |
| Golden plus ten source-checked candidates | 111 | 151 | 91 | 60 | 20 | 60.26% | 81.98% | 69.47% |

The earlier [audit of 97 unmatched outputs](../unmatched-all-edits-audit.csv) flagged ten possible omissions from the original Golden. We included all ten in the expanded reference, including the one that the final pass dropped. Each entry has a PDF page, exact text span, quotation and audited catalog ID. This reference comes from reviewing TraceGraph predictions on these three PDFs, so other extractors should also be scored on the original 101 for a like-for-like comparison. Some entries are attributed proposals or projections. The score measures extraction of the stated terms. [Ten supplementary entries](reference-additions.json)

The release preserved all 82 original matches. Nine additional catalog IDs retained the audited claim verbatim. The only loss among the ten was the projected median compensation near $80,000 including benefits at Bally’s Bronx, on page 13 of the selection report. On page 5, Bally’s and Hard Rock proposed different slot tax rates in a single sentence. Both predictions cite the entire sentence, so the unchanged span scorer interchanges their IDs. Checking the actor and the 30% and 25% rates confirms both claims. The lock lists both pairings so the automatic assignments can be inspected.

Replay both scores offline from the repository root.

```powershell
python -m evaluation.pdf-extraction-101.all_documents.release
```

Extending the existing tender review to the selection report and signed license reduced the experimental catalog from 161 to 151 records. All 82 previously matched Golden rows kept the same matched catalog IDs. An optional conservative confirmation of proposed exclusions retained 158 records, including a project-specific compensation estimate that the single review would have archived.

| Configuration | Records | Golden matches / 101 | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| Tender review only | 161 | 82 | 50.93% | 81.19% | 62.60% |
| One review across all documents | 151 | 82 | 54.30% | 81.19% | 65.08% |
| All documents, with archive confirmation | 158 | 82 | 51.90% | 81.19% | 63.32% |

## What changed

We reused the previous classifications and the 37 surviving tender decisions. GPT-5.4 reviewed the 110 source rows from the selection report and 19 from the license. Each request contained candidate claims, original quotations, neighboring text and document context. The LLM judged inclusion at the attributed modality, including proposals and signed obligations. KEEP and REVIEW preserve the exact input row. ARCHIVE removes it from the import file and retains the original row, cited page and reason in an audit file. A catalog group can have multiple source rows.

The single review archived eight selection report records and two license records. The three Board recommendations to consider named bidders for licensure are examples of procurement decisions. The archived set also included a projected median compensation of about $80,000 including benefits at Bally’s Bronx. A previous local audit had identified this as a plausible missing Golden entry. An unchanged benchmark recall therefore does not establish that every exclusion was harmless.

After inspecting these exclusions, we ran a second, conservative check on the ten proposed deletions. It agreed to archive the three licensing recommendations. It marked the other seven for review and kept them in the catalog. Those seven include the compensation estimate and the license duration, as well as a consultant's hypothetical tax-revenue scenario. The conservative check consequently retains some background. Both exports are strict subsets of the initial 161-record catalog. The 18 previously archived tender records are outside this input.

The 101 references and one-to-one citation-span scorer are unchanged. No Golden labels or manual unmatched-output judgments were included in model requests. The additional model usage was 22 requests for the selection report and license, followed by two requests for the optional confirmation; web search was disabled. The three documents were already used during development.

## Run on new PDFs

For the selected policy, run the full sequence of source extraction, verified edits and one delete-only review across all document types. Import the resulting file using the standard PDF importer. Fresh model calls and database identity resolution can produce different counts for other runs.

```powershell
$pdfs = (Get-ChildItem ./inputs/*.pdf).FullName
python -m evaluation.pdf-extraction-101.all_documents.extract @pdfs --output ./pdf-release-run --openai-key-file ./openai-api-key.txt
python -m tracegraph.pdf_import ./pdf-release-run/reviewed.json @pdfs --report ./pdf-release-run/import.json --openai-key-file ./openai-api-key.txt
```

## Compare review policies on existing candidates

Use the optional PDF verification command before normal database import. Use a distinct output directory for each variant.

```powershell
$pdfs = (Get-ChildItem ./inputs/*.pdf).FullName
python -m tracegraph.tender_review ./pdf-run/reviewed.json @pdfs --scope all --output ./pdf-run/all-docs --openai-key-file ./openai-api-key.txt
python -m tracegraph.tender_review ./pdf-run/reviewed.json @pdfs --scope all --confirm-archives --output ./pdf-run/all-docs-confirmed --openai-key-file ./openai-api-key.txt
```

To reproduce both reported comparisons offline from the saved decisions, run `python -m evaluation.pdf-extraction-101.all_documents.score`. The paired experiment reuses the tender decisions exactly. Fresh online calls can vary.

[Single-pass import file](single-pass/reviewed.json) · [Single-pass exclusions](single-pass/archived.json) · [Confirmed import file](all-edits/reviewed.json) · [Confirmed exclusions](all-edits/archived.json) · [Candidate-level comparisons](candidate-decisions.csv) · [Scores by document](metrics.json)
