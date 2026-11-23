# B0 rules/patterns baseline: evaluation

Extractor `b0-rules-v1.1`, text extraction `pypdf.extract_text+layout_blocks_v1`, gold workbook sha256 `eb146d6733e4`, generated 2026-11-22T02:35:41+00:00.

Gold commitments: 32 (unaligned: 0). Scored predictions (gold-covered documents only): 155.

Split: `evaluation/splits/resplit_v2.json`. Gold policy: `validated_only: rows with 'manually annotated valid?' = 1 are gold; extractions of rows marked 0 or blank count as false positives`. 68 gold rows were excluded; predictions that land on them account for 30 of the lenient false positives.

The gold set samples commitments rather than labelling every commitment, so unmatched predictions count as false positives and **precision is a lower bound**. `annotated pages` restricts predictions to pages that hold at least one gold commitment. *Lenient* matching needs the prediction to cover at least 50% of the gold span; *strict* needs span IoU of at least 0.5.

## Commitment-level precision / recall / F1

| Matching | Scope | P | R | F1 | P (annotated pages) | F1 (annotated pages) | TP | FP | FP (annotated pages) | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lenient | all | 0.129 | 0.625 | 0.214 | 0.182 | 0.282 | 20 | 135 | 90 | 12 |
| lenient | anchor | 0.119 | 0.571 | 0.198 | 0.222 | 0.320 | 8 | 59 | 28 | 6 |
| lenient | heldout | 0.136 | 0.667 | 0.226 | 0.162 | 0.261 | 12 | 76 | 62 | 6 |
| strict | all | 0.116 | 0.562 | 0.193 | 0.164 | 0.254 | 18 | 137 | 92 | 14 |
| strict | anchor | 0.104 | 0.500 | 0.173 | 0.194 | 0.280 | 7 | 60 | 29 | 7 |
| strict | heldout | 0.125 | 0.611 | 0.207 | 0.149 | 0.239 | 11 | 77 | 63 | 7 |

By gold split (lenient). Precision counts predictions on pages that hold that split's gold; a page with gold from two splits counts for both.

| Split | P (split pages) | R | F1 (split pages) | TP | FP (split pages) | FN |
| --- | --- | --- | --- | --- | --- | --- |
| dev | 0.169 | 0.647 | 0.268 | 11 | 54 | 6 |
| test | 0.120 | 0.600 | 0.200 | 9 | 66 | 6 |

## Field-level results: all (lenient matches, n = 20)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 0.923 | 0.800 | 0.857 | 0.929 | 0.701 |
| term_periodicity | 0.857 | 0.353 | 0.500 | 0.583 | 0.527 |
| geographic_scope | 0.750 | 0.500 | 0.600 | 0.800 | 0.658 |
| beneficiary_category | 0.529 | 0.450 | 0.486 | 0.919 | 0.541 |
| beneficiary_named | 0.500 | 0.111 | 0.182 | 0.182 | 0.857 |
| condition_contingency | 0.500 | 0.500 | 0.500 | 0.500 | 0.636 |
| milestone | 0.667 | 0.400 | 0.500 | 0.750 | 0.741 |
| **attribute macro-F1** | | | **0.578** | | |
| obligation_type (classification) | | accuracy 0.750 | macro-F1 0.773 | | |
| evidence_status (classification) | | accuracy 0.250 | macro-F1 0.103 | | |

## Field-level results: heldout (lenient matches, n = 12)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 1.000 | 0.900 | 0.947 | 0.947 | 0.713 |
| term_periodicity | 0.800 | 0.400 | 0.533 | 0.667 | 0.457 |
| geographic_scope | 0.714 | 0.500 | 0.588 | 0.824 | 0.657 |
| beneficiary_category | 0.500 | 0.417 | 0.455 | 0.909 | 0.580 |
| beneficiary_named | 0.000 | 0.000 | 0.000 | 0.000 | - |
| condition_contingency | 0.000 | 0.000 | 0.000 | 0.000 | - |
| milestone | 0.000 | 0.000 | 0.000 | 1.000 | 0.222 |
| **attribute macro-F1** | | | **0.440** | | |
| obligation_type (classification) | | accuracy 0.667 | macro-F1 0.679 | | |
| evidence_status (classification) | | accuracy 0.083 | macro-F1 0.083 | | |

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

Macro-F1 0.773 averages the 6 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro |
| --- | --- | --- | --- | --- | --- | --- |
| Binding / license condition | 1.000 | 1.000 | 1.000 | 4 | 4 | yes |
| Contractual commitment | 0.583 | 1.000 | 0.737 | 7 | 12 | yes |
| Target / goal | 1.000 | 0.333 | 0.500 | 3 | 1 | yes |
| Best effort | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| Forecast / projection | 1.000 | 1.000 | 1.000 | 1 | 1 | yes |
| Contingent commitment | 1.000 | 1.000 | 1.000 | 1 | 1 | yes |
| Reported accomplishment | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| Unresolved / ambiguous | 1.000 | 0.250 | 0.400 | 4 | 1 | yes |

## Evidence status, per class (all matched gold, lenient)

Macro-F1 0.103 averages the 5 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro |
| --- | --- | --- | --- | --- | --- | --- |
| corroborated | 0.000 | 0.000 | 0.000 | 1 | 0 | yes |
| partially corroborated | 0.000 | 0.000 | 0.000 | 3 | 0 | yes |
| self-reported only | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| conflicting | 0.000 | 0.000 | 0.000 | 1 | 0 | yes |
| no public evidence located | 0.210 | 1.000 | 0.348 | 4 | 19 | yes |
| source unavailable | 0.000 | 0.000 | 0.000 | 0 | 0 | no |
| not yet due | 1.000 | 0.091 | 0.167 | 11 | 1 | yes |
| not applicable | 0.000 | 0.000 | 0.000 | 0 | 0 | no |

## Corpus

| Document | Pages | Passages | Predictions | Scored |
| --- | --- | --- | --- | --- |
| RFA-2023-01-03 | 70 | 1095 | 0 | yes |
| GFLB-SEL-2025-12-01 | 30 | 439 | 136 | yes |
| BY-LAWS-COMMUNITY-BOARD-10-AS-OF-10-03-2019 | 6 | 0 | 0 | no |
| CONTACT-QUEENS-CB10 | 1 | 5 | 0 | no |
| GENTINGNEWYORKLLC-PRIVATECOMPANY | 2 | 27 | 0 | no |
| RESORTSWORLD116 | 4 | 5 | 0 | no |
| NYSGC-RWNYC-LIC-2025-12-19 | 5 | 56 | 19 | yes |
