# B0 rules/patterns baseline: evaluation

Extractor `b0-rules-v1.0`, text extraction `pypdf.extract_text+layout_blocks_v1`, gold workbook sha256 `eb146d6733e4`, generated 2026-11-18T07:07:10+00:00.

Gold commitments: 100 (unaligned: 0). Scored predictions (gold-covered documents only): 583.

The gold set samples commitments rather than labelling every commitment, so unmatched predictions count as false positives and **precision is a lower bound**. `annotated pages` restricts predictions to pages that hold at least one gold commitment. *Lenient* matching needs the prediction to cover at least 50% of the gold span; *strict* needs span IoU of at least 0.5.

## Commitment-level precision / recall / F1

| Matching | Scope | P | R | F1 | P (annotated pages) | F1 (annotated pages) | TP | FP | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lenient | all | 0.134 | 0.780 | 0.228 | 0.299 | 0.432 | 78 | 505 | 22 |
| lenient | anchor | 0.113 | 0.794 | 0.199 | 0.316 | 0.452 | 54 | 422 | 14 |
| lenient | heldout | 0.224 | 0.750 | 0.345 | 0.267 | 0.393 | 24 | 83 | 8 |
| strict | all | 0.118 | 0.690 | 0.202 | 0.264 | 0.382 | 69 | 514 | 31 |
| strict | anchor | 0.107 | 0.750 | 0.188 | 0.298 | 0.427 | 51 | 425 | 17 |
| strict | heldout | 0.168 | 0.562 | 0.259 | 0.200 | 0.295 | 18 | 89 | 14 |

Recall by gold split (lenient):

- anchor_dev: 1.000 (20/20)
- anchor_test: 0.708 (34/48)
- heldout_provisional: 0.750 (24/32)
- human_validated: 0.812 (26/32)

## Field-level results: all (lenient matches, n = 78)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.974 | 0.974 | 0.974 | 0.987 | 0.824 |
| amount_value | 0.973 | 0.783 | 0.868 | 0.892 | 0.684 |
| term_periodicity | 0.625 | 0.156 | 0.250 | 0.400 | 0.501 |
| geographic_scope | 0.905 | 0.452 | 0.603 | 0.667 | 0.788 |
| beneficiary_category | 0.696 | 0.500 | 0.582 | 0.836 | 0.588 |
| beneficiary_named | 0.800 | 0.250 | 0.381 | 0.381 | 0.765 |
| condition_contingency | 0.778 | 0.875 | 0.824 | 0.824 | 0.702 |
| milestone | 0.800 | 0.333 | 0.471 | 0.588 | 0.769 |
| **attribute macro-F1** | | | **0.619** | | |
| obligation_type (classification) | | accuracy 0.756 | macro-F1 0.749 | | |
| evidence_status (classification) | | accuracy 0.385 | macro-F1 0.081 | | |

## Field-level results: anchor_test (lenient matches, n = 34)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 0.939 | 0.939 | 0.939 | 0.970 | 0.752 |
| amount_value | 0.929 | 0.722 | 0.812 | 0.875 | 0.566 |
| term_periodicity | 0.333 | 0.071 | 0.118 | 0.353 | 0.375 |
| geographic_scope | 1.000 | 0.389 | 0.560 | 0.560 | 0.873 |
| beneficiary_category | 0.591 | 0.382 | 0.464 | 0.786 | 0.537 |
| beneficiary_named | 1.000 | 0.500 | 0.667 | 0.667 | 0.800 |
| condition_contingency | 0.750 | 1.000 | 0.857 | 0.857 | 0.733 |
| milestone | 1.000 | 0.333 | 0.500 | 0.500 | 0.962 |
| **attribute macro-F1** | | | **0.615** | | |
| obligation_type (classification) | | accuracy 0.765 | macro-F1 0.675 | | |
| evidence_status (classification) | | accuracy 0.647 | macro-F1 0.131 | | |

## Field-level results: heldout (lenient matches, n = 24)

| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |
| --- | --- | --- | --- | --- | --- |
| responsible_entity | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| amount_value | 1.000 | 0.750 | 0.857 | 0.857 | 0.722 |
| term_periodicity | 0.800 | 0.222 | 0.348 | 0.435 | 0.457 |
| geographic_scope | 0.818 | 0.500 | 0.621 | 0.759 | 0.727 |
| beneficiary_category | 0.667 | 0.500 | 0.571 | 0.857 | 0.586 |
| beneficiary_named | 0.500 | 0.091 | 0.154 | 0.154 | 0.545 |
| condition_contingency | 0.000 | 0.000 | 0.000 | 0.000 | - |
| milestone | 0.000 | 0.000 | 0.000 | 1.000 | 0.222 |
| **attribute macro-F1** | | | **0.444** | | |
| obligation_type (classification) | | accuracy 0.625 | macro-F1 0.502 | | |
| evidence_status (classification) | | accuracy 0.042 | macro-F1 0.020 | | |

## Field-level results: human_validated (lenient matches, n = 26)

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

## Obligation class, per class (all matched gold, lenient)

| Class | P | R | F1 | Gold support |
| --- | --- | --- | --- | --- |
| Binding / license condition | 0.826 | 0.905 | 0.864 | 21 |
| Contractual commitment | 0.600 | 0.857 | 0.706 | 14 |
| Target / goal | 1.000 | 0.625 | 0.769 | 8 |
| Best effort | 1.000 | 1.000 | 1.000 | 1 |
| Forecast / projection | 0.800 | 0.923 | 0.857 | 13 |
| Contingent commitment | 0.500 | 0.667 | 0.571 | 3 |
| Reported accomplishment | 1.000 | 0.500 | 0.667 | 2 |
| Unresolved / ambiguous | 0.778 | 0.438 | 0.560 | 16 |

## Corpus

| Document | Pages | Passages | Predictions | Scored |
| --- | --- | --- | --- | --- |
| RFA-2023-01-03 | 70 | 1095 | 358 | yes |
| GFLB-SEL-2025-12-01 | 30 | 439 | 206 | yes |
| BY-LAWS-COMMUNITY-BOARD-10-AS-OF-10-03-2019 | 6 | 0 | 0 | no |
| CONTACT-QUEENS-CB10 | 1 | 5 | 0 | no |
| GENTINGNEWYORKLLC-PRIVATECOMPANY | 2 | 27 | 0 | no |
| RESORTSWORLD116 | 4 | 5 | 0 | no |
| NYSGC-RWNYC-LIC-2025-12-19 | 5 | 56 | 19 | yes |
