# B0 rules/patterns baseline: evaluation

Extractor `b0-rules-v1.2`, text extraction `pypdf.extract_text+layout_blocks_v1`, gold workbook sha256 `eb146d6733e4`, generated 2026-11-22T03:13:05+00:00.

Gold commitments: 32 (unaligned: 0). Scored predictions (gold-covered documents only): 446.

Split: `evaluation/splits/resplit_v2.json`. Gold policy: `validated_only: rows with 'manually annotated valid?' = 1 are gold; extractions of rows marked 0 or blank count as false positives`. 68 gold rows were excluded; predictions that land on them account for 34 of the lenient false positives.

The gold set samples commitments rather than labelling every commitment, so unmatched predictions count as false positives and **precision is a lower bound**. `annotated pages` restricts predictions to pages that hold at least one gold commitment. *Lenient* matching needs the prediction to cover at least 50% of the gold span; *strict* needs span IoU of at least 0.5.

## Commitment-level precision / recall / F1

| Matching | Scope | P | R | F1 | P (annotated pages) | F1 (annotated pages) | TP | FP | FP (annotated pages) | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lenient | all | 0.058 | 0.812 | 0.109 | 0.206 | 0.329 | 26 | 420 | 100 | 6 |
| lenient | anchor | 0.031 | 0.786 | 0.060 | 0.211 | 0.333 | 11 | 344 | 41 | 3 |
| lenient | heldout | 0.165 | 0.833 | 0.275 | 0.203 | 0.326 | 15 | 76 | 59 | 3 |
| strict | all | 0.049 | 0.688 | 0.092 | 0.175 | 0.279 | 22 | 424 | 104 | 10 |
| strict | anchor | 0.028 | 0.714 | 0.054 | 0.192 | 0.303 | 10 | 345 | 42 | 4 |
| strict | heldout | 0.132 | 0.667 | 0.220 | 0.162 | 0.261 | 12 | 79 | 62 | 6 |

By gold split (lenient). Precision counts predictions on pages that hold that split's gold; a page with gold from two splits counts for both.

| Split | P (split pages) | R | F1 (split pages) | TP | FP (split pages) | FN |
| --- | --- | --- | --- | --- | --- | --- |
| dev | 0.205 | 1.000 | 0.340 | 17 | 66 | 0 |
| test | 0.123 | 0.600 | 0.204 | 9 | 64 | 6 |

The rules were tuned on `dev`, so `dev` results and any scope that includes them (such as `all`) are optimistic. Report the `test` row and the `test` field-level section.

## Field-level results: all (lenient matches, n = 26)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.923 | 0.960 | 0.941 | 0.980 | 0.927 |
| amount_value | 0.938 | 0.714 | 0.811 | 0.865 | 0.709 |
| term_periodicity | 0.857 | 0.316 | 0.462 | 0.538 | 0.527 |
| geographic_scope | 0.800 | 0.471 | 0.593 | 0.741 | 0.667 |
| beneficiary_category | 0.571 | 0.462 | 0.511 | 0.894 | 0.549 |
| beneficiary_named | 0.500 | 0.083 | 0.143 | 0.143 | 0.857 |
| condition_contingency | 0.500 | 0.500 | 0.500 | 0.500 | 0.636 |
| milestone | 0.667 | 0.400 | 0.500 | 0.750 | 0.741 |
| **attribute macro-F1** | | | **0.557** | | |
| obligation_type (classification) | | accuracy 0.692 | macro-F1 0.692 | | |
| evidence_status (classification) | | accuracy 0.269 | macro-F1 0.077 | | |

## Field-level results: heldout (lenient matches, n = 15)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 1.000 | 0.769 | 0.870 | 0.870 | 0.741 |
| term_periodicity | 0.800 | 0.400 | 0.533 | 0.667 | 0.457 |
| geographic_scope | 0.750 | 0.500 | 0.600 | 0.800 | 0.625 |
| beneficiary_category | 0.500 | 0.400 | 0.444 | 0.889 | 0.539 |
| beneficiary_named | 0.000 | 0.000 | 0.000 | 0.000 | - |
| condition_contingency | 0.000 | 0.000 | 0.000 | 0.000 | - |
| milestone | 0.000 | 0.000 | 0.000 | 1.000 | 0.222 |
| **attribute macro-F1** | | | **0.431** | | |
| obligation_type (classification) | | accuracy 0.600 | macro-F1 0.559 | | |
| evidence_status (classification) | | accuracy 0.067 | macro-F1 0.038 | | |

## Field-level results: test (lenient matches, n = 9)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 0.833 | 0.625 | 0.714 | 0.857 | 0.507 |
| term_periodicity | 0.667 | 0.286 | 0.400 | 0.600 | 0.540 |
| geographic_scope | 0.800 | 0.571 | 0.667 | 0.833 | 0.693 |
| beneficiary_category | 0.250 | 0.222 | 0.235 | 0.941 | 0.337 |
| beneficiary_named | 0.000 | 0.000 | 0.000 | 0.000 | - |
| condition_contingency | 0.000 | 0.000 | 0.000 | 0.000 | - |
| milestone | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| **attribute macro-F1** | | | **0.502** | | |
| obligation_type (classification) | | accuracy 0.667 | macro-F1 0.583 | | |
| evidence_status (classification) | | accuracy 0.222 | macro-F1 0.091 | | |

## Obligation class, per class (all matched gold, lenient)

Macro-F1 0.692 averages the 6 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro |
| --- | --- | --- | --- | --- | --- | --- |
| Binding / license condition | 0.833 | 1.000 | 0.909 | 5 | 6 | yes |
| Contractual commitment | 0.583 | 0.875 | 0.700 | 8 | 12 | yes |
| Target / goal | 1.000 | 0.333 | 0.500 | 3 | 1 | yes |
| Best effort | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| Forecast / projection | 0.333 | 1.000 | 0.500 | 1 | 3 | yes |
| Contingent commitment | 1.000 | 1.000 | 1.000 | 1 | 1 | yes |
| Reported accomplishment | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| Unresolved / ambiguous | 1.000 | 0.375 | 0.545 | 8 | 3 | yes |

## Evidence status, per class (all matched gold, lenient)

Macro-F1 0.077 averages the 7 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro |
| --- | --- | --- | --- | --- | --- | --- |
| corroborated | 0.000 | 0.000 | 0.000 | 1 | 0 | yes |
| partially corroborated | 0.000 | 0.000 | 0.000 | 3 | 0 | yes |
| self-reported only | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| conflicting | 0.000 | 0.000 | 0.000 | 1 | 0 | yes |
| no public evidence located | 0.240 | 1.000 | 0.387 | 6 | 25 | yes |
| source unavailable | 0.000 | 0.000 | 0.000 | 1 | 0 | yes |
| not yet due | 1.000 | 0.083 | 0.154 | 12 | 1 | yes |
| not applicable | 0.000 | 0.000 | 0.000 | 2 | 0 | yes |

## Corpus

| Document | Pages | Passages | Predictions | Scored |
| --- | --- | --- | --- | --- |
| RFA-2023-01-03 | 70 | 1095 | 257 | yes |
| GFLB-SEL-2025-12-01 | 30 | 439 | 172 | yes |
| BY-LAWS-COMMUNITY-BOARD-10-AS-OF-10-03-2019 | 6 | 0 | 0 | no |
| CONTACT-QUEENS-CB10 | 1 | 5 | 0 | no |
| GENTINGNEWYORKLLC-PRIVATECOMPANY | 2 | 27 | 0 | no |
| RESORTSWORLD116 | 4 | 5 | 0 | no |
| NYSGC-RWNYC-LIC-2025-12-19 | 5 | 56 | 17 | yes |
