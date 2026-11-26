# Tender verification pass

On the original three PDFs, adding a solicitation-specific inclusion review to the all-edits experiment preserved the same 82 Golden matches and reduced the catalog from 179 to 161 records. The scorer, 101 reference rows and one-to-one matching policy were identical across all comparisons.

| Input and policy | Catalog records | Golden matches | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| Frozen release | 184 | 81 | 44.0% | 80.2% | 56.8% |
| Frozen release + tender review | 160 | 81 | 50.6% | 80.2% | 62.1% |
| All edits | 179 | 82 | 45.81% | 81.19% | 58.57% |
| All edits + tender review | 161 | 82 | **50.93%** | **81.19%** | **62.60%** |

GPT-5.4 classified each document using its opening and headings. It routed the application solicitation to review and passed through the selection report and the issued license. For the solicitation it read each candidate, the cited passage and the surrounding text, then returned KEEP, ARCHIVE or REVIEW with a citation and a reason. Only ARCHIVE removes a row from the import file. Candidates marked REVIEW stay visible. Python verifies source coordinates, returned IDs and the output structure. The model saw public source text and candidate claims. Golden was used locally for scoring.

On the all-edits input the solicitation alone went from 14/55 to **14/37** (25.45% to 37.84% precision). Its 14/19 recall stayed constant. The 18 archived rows include procurement waivers, requirements to supply handwriting samples, and routine licensing conditions. The two REVIEW rows concern what an applicant must present about community impacts and renewable power. All predictions from the other two PDFs passed through.

The prior [review of unmatched outputs](../unmatched-all-edits-audit.csv) classified 13 of the 18 archived groups as process, background or routine compliance, and five as overlapping information or finer detail. One of those five states the **$500 million** license fee. The separately retained payment obligation specifies the **30-day** deadline. Span matching still credits A011, so the unchanged matching score can conceal a missing qualifier in an individual catalog entry. All archived rows include the original claim, page, source excerpts and review reason. The pass does not automatically attach archived details to a retained obligation.

Both comparisons reused the same source-only decisions on the union of distinct candidate rows. These are paired comparisons on one solicitation, not independent runs on multiple tenders. The run made 15 requests with web tools disabled. The frozen release remains independently reproducible.

## Use it before import

After the existing extraction step, run from the repository root with a separate output directory.

```powershell
$pdfs = (Get-ChildItem ./inputs/*.pdf).FullName
python -m tracegraph.tender_review ./pdf-run/reviewed.json @pdfs --output ./pdf-run/tender --openai-key-file ./openai-api-key.txt
```

Use the new `reviewed.json` with the normal PDF importer. The output directory also contains `archived.json` and `tender-decisions.json`. To reproduce the reported scores from recorded decisions without API calls, run `python -m evaluation.pdf-extraction-101.tender.score`.

[Scored import file](all-edits/reviewed.json) · [Archived rows with passages](all-edits/archived.json) · [Candidate decisions](candidate-decisions.csv) · [Full metrics](metrics.json)
