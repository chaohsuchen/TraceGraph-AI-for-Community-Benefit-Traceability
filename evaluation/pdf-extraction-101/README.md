# Trusted-PDF commitment extraction

The [frozen release](freeze/README.md) matches **81 of 101** annotated rows across 184 catalog records. The table below records the earlier import used as the starting point.

The [follow-up diagnostic](diagnostic/README.md) traces the 101 reference rows through extraction, review, and catalog matching, then compares six offline policies on the frozen sources.

On November 25, 2026, TraceGraph read 105 pages across the same three PDFs used to score B0 v1.2. The extraction and identity models were GPT-5.4 with low reasoning effort and no web tools. The 101 workbook rows entered the local scorer after extraction. No Golden commitments or verdicts were imported.

| Output | Predictions | Golden hits / 101 | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| B0 v1.2 | 446 | 60 | 13.45% | 59.41% | 21.94% |
| MVP after first extraction | 258 | 72 | 27.91% | 71.29% | 40.11% |
| MVP after source review | 180 | 62 | 34.44% | 61.39% | 44.13% |
| MVP after catalog identity resolution | 174 | 62 | 35.63% | 61.39% | 45.09% |

The initial catalog merge had 58 hits among 158 records. Inspection exposed an overmerge involving a construction labor agreement and a workforce composition target. We checked all 23 proposed links directly against their final canonical descriptions. The second model check accepted seven and disputed 16. We reconsidered the disputed items, preserved the original commitment IDs and retained the original PDF excerpts. The final line includes this development adjustment. These three documents were already familiar during design, so this run does not measure generalization to unseen PDFs.

| Final catalog by document | PDF pages | Records citing it | Golden hits | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| Casino request for applications | 70 | 82 | 9 / 19 | 10.98% | 47.37% |
| Location Board selection report | 30 | 78 | 41 / 70 | 52.56% | 58.57% |
| Resorts World license | 5 | 15 | 12 / 12 | 80.00% | 100.00% |

One catalog record cites two documents and counts once in the overall row. PostgreSQL contains 173 new records and one previously existing commitment linked to the batch, with 260 source passages. None of the new records had a delivery assessment or a research job after import. Replaying all 180 accepted input items reused their stored assignments.

## Scoring and interpretation

An eligible pair shares a document and covers at least half the Golden quote. Overlapping citations are unioned. Text between disjoint quotes receives no credit. Candidate pairs are sorted by coverage and intersection-over-union, with deterministic ID tie breaks. Each prediction and each Golden row can earn one hit. The scorer preserves the two physical A019 rows. We replayed the B0 output at commit `035c759` under the same code and reproduced its 60 matches. An unmatched prediction counts against precision even when the workbook lacks an equivalent label.

The two-stage review removed 78 candidates and lost ten Golden hits. It screened out some regulator-recommended obligations, including H013 and H024. The RFA also retained examples of administrative steps such as attending a hearing and conducting application background checks. Telling a specific, ongoing public duty apart from a procedural step remains the largest source of error here. The final result cleared the 60-hit goal and fell short of the proposed 40% precision target.

These scores measure extraction by source-span overlap. They do not grade structured field accuracy, delivery evidence coverage or verdict correctness. The lookup tests used real PostgreSQL and Chromium with a deterministic research provider in a disposable schema.

## Reproduce the score offline

Run from the repository root. The package includes aligned reference spans, both frozen extraction stages, item-to-catalog assignments, final metrics, database counts and the input manifest.

```powershell
$run = 'evaluation/pdf-extraction-101'
python -m evaluation.score_pdf_extraction "$run/reference.json" $run --import-report "$run/import.json" --output "$env:TEMP/tracegraph-pdf-scores.json"
```

The three source PDFs are under `docs/tier1/` at B0 commit `035c759`. The manifest pins their SHA-256 hashes. To run a fresh model extraction or import the frozen predictions into another database, use the commands in the repository README.
