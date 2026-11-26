# PDF catalog freeze 1

On the three public PDFs and 101 Golden annotation rows, the **all-edits experimental configuration** produced 179 catalog records and matched **82 rows**, with **45.81% precision, 81.19% recall and 58.57% F1**. Among the seven configurations in the final round, it had the highest precision while maintaining or improving the frozen release’s aggregate recall. The frozen release matched 81 rows in 184 records, with 44.02% precision, 80.20% recall and 56.84% F1. The experiment therefore gained one net match and produced six fewer unmatched records.

The aggregate comparison allows one newly found row to offset a lost row. The earlier release decision required every previously found row to remain matched. Both results measure commitment extraction by source-span matching on documents used during development. Delivery evidence retrieval and verdict prediction have separate evaluations.

The default PDF extractor now runs first-pass extraction and review, checks every window for omitted commitments, and adjudicates newly proposed items and disagreements using the source text. Generated claims retain their cited locations. A PDF can establish an attributable promise, plan or condition before delivery research begins.

## Final experiment

Before the last model run we recorded the [seven comparison policies and selection rule](ROUND.md). Losing any one of the 81 previously matched annotation rows disqualified a policy. Among qualified policies, precision determined the winner. The last round read all 32 windows and made 37 proposals. A separate source-based review approved 12 additions, 15 archives and two merges. The model received public source material and candidates. Golden entered only the offline scorer.

| Policy | Outputs | Golden matches | Added / lost against 81 | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| **Selected release** | **184** | **81** | **0 / 0** | **44.02%** | **80.20%** | **56.84%** |
| Additions only | 196 | 83 | 2 / 0 | 42.35% | 82.18% | 55.89% |
| Archives only | 169 | 80 | 0 / 1 | 47.34% | 79.21% | 59.26% |
| Additions and archives | 181 | 82 | 2 / 1 | 45.30% | 81.19% | 58.16% |
| Additions, archives and complementary fields | 180 | 82 | 2 / 1 | 45.56% | 81.19% | 58.36% |
| All edits | 179 | 82 | 2 / 1 | 45.81% | 81.19% | 58.57% |
| Archives and both merge types | 167 | 80 | 0 / 1 | 47.90% | 79.21% | 59.70% |

The all-edits policy gained A020 (record CAC promises as amendments) and A032 (estimated median full-time compensation of about $150,000). It archived A050, the reported project labor and collective bargaining agreements with four unions covering 84% of the workforce. The reviewer interpreted these agreements as existing conditions. Losing that documented project fact was the deciding regression. No accepted policy reached 50% precision while preserving the original 81. New experimental edits remain in the audit for inspection.

## Frozen outputs and verification

The [reviewed source rows](reviewed.json) are ready for the existing PDF importer. The [frozen catalog assignments](catalog-groups.json) map their content keys to catalog groups for repeatable scoring. There are 189 source rows, 184 distinct catalog groups and 257 citation occurrences. The scorer uses the original one-to-one source-span policy and all 101 physical annotation rows. Fresh API calls and identity resolution against a different database can produce different outputs.

Run the following command from the repository root to verify the pinned inputs and reproduce every candidate policy without API calls or a database.

```powershell
python -m evaluation.pdf-extraction-101.freeze.score
```

To test the shipping extraction entry point with the recorded source responses, pass a directory containing the three PDFs listed in the [source manifest](../manifest.json).

```powershell
python -m evaluation.pdf-extraction-101.freeze.verify_pipeline ./inputs
```

The entry-point replay reproduces all 189 source rows, validates each excerpt against the original PDF and exercises a cached retry. PostgreSQL integration and Chromium E2E tests used a disposable schema and a fixture research provider. They covered duplicate import, opening the cited PDF and reusing a completed assessment on repeat lookup. The last source-only model experiment logged 39 requests, 152,216 input tokens and 18,601 output tokens. See the [policy comparison](comparison.json), [source decisions](source-decisions.json), [usage](usage.json) and [freeze lock](freeze.lock.json).
