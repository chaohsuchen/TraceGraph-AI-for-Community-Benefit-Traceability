# B0 rules/patterns baseline: evaluation

Extractor `b0-rules`, text extraction `pypdf.extract_text+layout_blocks_v1`, gold workbook sha256 `eb146d6733e4`, generated 2026-11-27T03:20:22+00:00.

Gold commitments: 100 (unaligned: 0). Scored predictions (gold-covered documents only): 90.

Split: `evaluation/splits/dev_test_split.json`. Gold policy: `all_rows: every workbook gold row is a positive`.

The gold set samples commitments rather than labelling every commitment, so unmatched predictions count as false positives and **precision is a lower bound**. `annotated pages` restricts predictions to pages that hold at least one gold commitment. *Lenient* matching needs the prediction to cover at least 50% of the gold span; *strict* needs span IoU of at least 0.5.

## Commitment-level precision / recall / F1

| Matching | Scope | P | R | F1 | P (annotated pages) | F1 (annotated pages) | TP | FP | FP (annotated pages) | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lenient | all | 0.622 | 0.560 | 0.590 | 0.709 | 0.626 | 56 | 34 | 23 | 44 |
| lenient | anchor | 0.647 | 0.485 | 0.555 | 0.673 | 0.564 | 33 | 18 | 16 | 35 |
| lenient | heldout | 0.590 | 0.719 | 0.648 | 0.767 | 0.742 | 23 | 16 | 7 | 9 |
| strict | all | 0.544 | 0.490 | 0.516 | 0.620 | 0.547 | 49 | 41 | 30 | 51 |
| strict | anchor | 0.588 | 0.441 | 0.504 | 0.612 | 0.513 | 30 | 21 | 19 | 38 |
| strict | heldout | 0.487 | 0.594 | 0.535 | 0.633 | 0.613 | 19 | 20 | 11 | 13 |

By gold split (lenient). Precision counts predictions on pages that hold that split's gold; a page with gold from two splits counts for both.

| Split | P (split pages) | R | F1 (split pages) | TP | FP (split pages) | FN |
| --- | --- | --- | --- | --- | --- | --- |
| dev | 0.950 | 0.731 | 0.826 | 38 | 2 | 14 |
| test | 0.462 | 0.375 | 0.414 | 18 | 21 | 30 |

The rules were tuned on `dev`, so `dev` results and any scope that includes them (such as `all`) are optimistic. Report the `test` row and the `test` field-level section.

## Classification and fields by split (lenient matches)

Scored on the gold commitments each split's predictions matched, so `n` is small and one commitment moves accuracy by 1/n.

| Split | n matched | Obligation accuracy | Obligation macro-F1 | Evidence accuracy | Evidence macro-F1 | Attribute macro-F1 | Attribute macro accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 38 | 0.789 | 0.821 | 0.395 | 0.104 | 0.579 | 0.766 |
| test | 18 | 0.833 | 0.741 | 0.389 | 0.083 | 0.628 | 0.764 |

## Field-level results: all (lenient matches, n = 56)

| Field | P | R | F1 | Accuracy | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.982 | 0.982 | 0.982 | 0.964 | 0.982 | 0.919 |
| amount_value | 1.000 | 0.774 | 0.873 | 0.875 | 0.873 | 0.675 |
| term_periodicity | 0.714 | 0.227 | 0.345 | 0.393 | 0.483 | 0.583 |
| geographic_scope | 0.929 | 0.464 | 0.619 | 0.732 | 0.667 | 0.778 |
| beneficiary_category | 0.682 | 0.536 | 0.600 | 0.536 | 0.880 | 0.599 |
| beneficiary_named | 1.000 | 0.222 | 0.364 | 0.750 | 0.364 | 0.796 |
| condition_contingency | 0.833 | 0.714 | 0.769 | 0.946 | 0.769 | 0.690 |
| milestone | 0.500 | 0.200 | 0.286 | 0.929 | 0.571 | 0.611 |
| **attribute macro** | | | **0.605** | **0.766** | | |
| obligation_type (classification) | | | macro-F1 0.820 | 0.804 | | |
| evidence_status (classification) | | | macro-F1 0.087 | 0.393 | | |

## Field-level results: heldout (lenient matches, n = 23)

| Field | P | R | F1 | Accuracy | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 1.000 | 0.765 | 0.867 | 0.826 | 0.867 | 0.698 |
| term_periodicity | 0.800 | 0.222 | 0.348 | 0.391 | 0.435 | 0.591 |
| geographic_scope | 0.900 | 0.529 | 0.667 | 0.652 | 0.741 | 0.800 |
| beneficiary_category | 0.667 | 0.522 | 0.585 | 0.522 | 0.878 | 0.582 |
| beneficiary_named | 1.000 | 0.182 | 0.308 | 0.609 | 0.308 | 0.734 |
| condition_contingency | 0.500 | 0.500 | 0.500 | 0.913 | 0.500 | 0.545 |
| milestone | 0.000 | 0.000 | 0.000 | 0.957 | 1.000 | 0.222 |
| **attribute macro** | | | **0.534** | **0.734** | | |
| obligation_type (classification) | | | macro-F1 0.732 | 0.826 | | |
| evidence_status (classification) | | | macro-F1 0.049 | 0.130 | | |

## Field-level results: human_validated (lenient matches, n = 23)

| Field | P | R | F1 | Accuracy | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.957 | 1.000 | 0.978 | 0.957 | 0.978 | 0.962 |
| amount_value | 1.000 | 0.750 | 0.857 | 0.826 | 0.857 | 0.729 |
| term_periodicity | 0.833 | 0.312 | 0.455 | 0.522 | 0.545 | 0.559 |
| geographic_scope | 0.875 | 0.538 | 0.667 | 0.739 | 0.762 | 0.750 |
| beneficiary_category | 0.632 | 0.522 | 0.571 | 0.522 | 0.905 | 0.568 |
| beneficiary_named | 1.000 | 0.167 | 0.286 | 0.565 | 0.286 | 0.890 |
| condition_contingency | 0.500 | 0.500 | 0.500 | 0.913 | 0.500 | 0.636 |
| milestone | 0.500 | 0.250 | 0.333 | 0.870 | 0.667 | 0.611 |
| **attribute macro** | | | **0.581** | **0.739** | | |
| obligation_type (classification) | | | macro-F1 0.894 | 0.870 | | |
| evidence_status (classification) | | | macro-F1 0.089 | 0.304 | | |

## Field-level results: dev (lenient matches, n = 38)

| Field | P | R | F1 | Accuracy | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.973 | 0.973 | 0.973 | 0.947 | 0.973 | 0.907 |
| amount_value | 1.000 | 0.809 | 0.895 | 0.895 | 0.895 | 0.682 |
| term_periodicity | 0.750 | 0.310 | 0.439 | 0.474 | 0.585 | 0.596 |
| geographic_scope | 0.889 | 0.381 | 0.533 | 0.658 | 0.600 | 0.676 |
| beneficiary_category | 0.710 | 0.579 | 0.638 | 0.579 | 0.899 | 0.633 |
| beneficiary_named | 1.000 | 0.214 | 0.353 | 0.711 | 0.353 | 0.775 |
| condition_contingency | 1.000 | 0.667 | 0.800 | 0.947 | 0.800 | 0.667 |
| milestone | 0.000 | 0.000 | 0.000 | 0.921 | 0.500 | 0.222 |
| **attribute macro** | | | **0.579** | **0.766** | | |
| obligation_type (classification) | | | macro-F1 0.821 | 0.789 | | |
| evidence_status (classification) | | | macro-F1 0.104 | 0.395 | | |

## Field-level results: test (lenient matches, n = 18)

| Field | P | R | F1 | Accuracy | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.943 |
| amount_value | 1.000 | 0.700 | 0.824 | 0.833 | 0.824 | 0.658 |
| term_periodicity | 0.500 | 0.067 | 0.118 | 0.222 | 0.235 | 0.500 |
| geographic_scope | 1.000 | 0.714 | 0.833 | 0.889 | 0.833 | 0.960 |
| beneficiary_category | 0.615 | 0.444 | 0.516 | 0.444 | 0.839 | 0.516 |
| beneficiary_named | 1.000 | 0.250 | 0.400 | 0.833 | 0.400 | 0.857 |
| condition_contingency | 0.500 | 1.000 | 0.667 | 0.944 | 0.667 | 0.783 |
| milestone | 1.000 | 0.500 | 0.667 | 0.944 | 0.667 | 1.000 |
| **attribute macro** | | | **0.628** | **0.764** | | |
| obligation_type (classification) | | | macro-F1 0.741 | 0.833 | | |
| evidence_status (classification) | | | macro-F1 0.083 | 0.389 | | |

## Obligation class, per class (all matched gold, lenient)

Macro-F1 0.820 averages the 7 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro | F1 dev | Support dev | F1 test | Support test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Binding / license condition | 0.800 | 0.727 | 0.762 | 11 | 10 | yes | 0.500 | 5 | 0.923 | 6 |
| Contractual commitment | 0.647 | 1.000 | 0.786 | 11 | 17 | yes | 0.778 | 7 | 0.800 | 4 |
| Target / goal | 1.000 | 0.750 | 0.857 | 4 | 3 | yes | 1.000 | 1 | 0.800 | 3 |
| Best effort | 0.000 | 0.000 | 0.000 | 0 | 0 | no | - | 0 | - | 0 |
| Forecast / projection | 0.900 | 1.000 | 0.947 | 9 | 10 | yes | 0.941 | 8 | 1.000 | 1 |
| Contingent commitment | 1.000 | 0.500 | 0.667 | 4 | 2 | yes | 0.800 | 3 | 0.000 | 1 |
| Reported accomplishment | 1.000 | 1.000 | 1.000 | 3 | 3 | yes | 1.000 | 2 | 1.000 | 1 |
| Unresolved / ambiguous | 0.818 | 0.643 | 0.720 | 14 | 11 | yes | 0.727 | 12 | 0.667 | 2 |

## Evidence status, per class (all matched gold, lenient)

Macro-F1 0.087 averages the 8 of 8 classes that have gold support among matched commitments; classes with no gold support are listed but not averaged.

| Class | P | R | F1 | Gold support | Predicted | In macro | F1 dev | Support dev | F1 test | Support test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| corroborated | 0.000 | 0.000 | 0.000 | 2 | 0 | yes | 0.000 | 1 | 0.000 | 1 |
| partially corroborated | 0.000 | 0.000 | 0.000 | 9 | 0 | yes | 0.000 | 6 | 0.000 | 3 |
| self-reported only | 0.000 | 0.000 | 0.000 | 1 | 3 | yes | - | 0 | 0.000 | 1 |
| conflicting | 0.000 | 0.000 | 0.000 | 4 | 0 | yes | 0.000 | 3 | 0.000 | 1 |
| no public evidence located | 0.404 | 1.000 | 0.575 | 21 | 52 | yes | 0.571 | 14 | 0.583 | 7 |
| source unavailable | 0.000 | 0.000 | 0.000 | 2 | 0 | yes | 0.000 | 1 | 0.000 | 1 |
| not yet due | 1.000 | 0.062 | 0.118 | 16 | 1 | yes | 0.154 | 12 | 0.000 | 4 |
| not applicable | 0.000 | 0.000 | 0.000 | 1 | 0 | yes | 0.000 | 1 | - | 0 |

## Corpus

| Document | Pages | Passages | Predictions | Scored |
| --- | --- | --- | --- | --- |
| RFA-2023-01-03 | 70 | 1097 | 6 | yes |
| GFLB-SEL-2025-12-01 | 30 | 446 | 71 | yes |
| BY-LAWS-COMMUNITY-BOARD-10-AS-OF-10-03-2019 | 6 | 0 | 0 | no |
| CONTACT-QUEENS-CB10 | 1 | 5 | 0 | no |
| GENTINGNEWYORKLLC-PRIVATECOMPANY | 2 | 27 | 0 | no |
| RESORTSWORLD116 | 4 | 5 | 0 | no |
| NYSGC-RWNYC-LIC-2025-12-19 | 5 | 56 | 13 | yes |
