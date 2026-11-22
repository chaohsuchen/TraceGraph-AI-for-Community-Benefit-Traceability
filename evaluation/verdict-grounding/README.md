# Fixed-evidence verdict regression

The [results report](report.md) covers all attempted fixes, their repeatability, transfer checks and the H017 integration probe. [Panel preregistration](transfer-protocol.md) records the case sets and why the additional trials were selected. The fixed labels and public narratives for each attempt live under `runs/`.

The six cases exercise the final assessment of the public passages users actually saw in the [previous evidence experiment](../evidence-study/report.md). That experiment's separately downloadable archive supplies the `evaluation/evidence-study` and `evaluation/golden22` inputs. Its product runtime, labelled **candidate** there, is the **control** in this experiment.

The test fixes the original public question, discovered draft, discovery scope hints, findings, conflict relationships, source ownership, citation order and November 22, 2026 assessment date. Each arm calls the normal `ClaudeReconciliationJudge.assess` entry point with a tool-disabled GPT‑5.4 Responses client, followed by the production `EvidenceReconciler._check_assessment` guard and its independent refutation check when applicable. The class name is historical. The selected provider is OpenAI. Every source passage is checked against the original call's trace before replay. Archived labels and judge fields stay outside the producer's input.

The separate evaluation step uses the frozen v4 judge and the original evidence judgment. The visible findings are identical in every arm. Only the displayed label, verdict and full reasoning are replaced in the judge packet. Native structural validation rechecks the model's condition judgements and citations. The same public reference hypotheses, original passages, rubric and model apply to all arms. All first attempts, metered calls and any structural retries are under `runs/<arm>/<case>/`. An offline audit checks input digests and recomputes each formal score. Each model call is a new generation; matching inputs do not guarantee identical prose or labels.

## Reproduction

Unpack the previous experiment's `runtime-candidate.zip` and its `evaluation/golden22/frozen-runtime.zip` into separate directories. Use the project Python environment. From the root of this checkout, run the following, substituting the actual local paths.

```powershell
python -B evaluation/verdict-grounding/replay.py audit --runtime . --study <previous-evidence-study>
python -B evaluation/verdict-grounding/summarize.py --study <previous-evidence-study> --judge-runtime <frozen-judge-runtime>
```

The second command uses the saved trials and requires no network. For a new paid replay, use a copy of this folder with an empty `runs` directory and the exact runtime for each arm. Frozen case receipts pin the source code hashes. Research and judging run separately.

For v4 through v7, `verify_panels.py --version <version> --study <previous-evidence-study> --judge-runtime <frozen-judge-runtime>` checks all 17 recorded scores with the network off, confirms the exact application module digests against the included overlays and previous runtime, and totals distinct metered calls. The `integration.py audit` command additionally takes `--arm integration-v5`, `integration-v6`, or `integration-v7`, `--runtime <frozen-judge-runtime>` and `--coverage <coverage-loss-directory>`. It verifies every original H017 excerpt alongside the four additions and recomputes its independent verdict grade. The [study report](report.md) includes the 417-call cost estimate, cross-version results and observed limitations.

For the final presentation update run `display_replay.py baseline` on the frozen v7 runtime, `compare` on the updated runtime, and `audit` on the pinned judge runtime. All three take `--study`, `--runtime` and `--coverage`, and run without network access. The separate `judge` stage adds a fresh grade only where the full public display changed. The [manifest](v7-display-replay.json) records each displayed digest.

```powershell
python -B evaluation/verdict-grounding/replay.py assess --study <previous-evidence-study> --runtime <product-runtime> --key-file <local-key-file> --arm control
python -B evaluation/verdict-grounding/replay.py judge --study <previous-evidence-study> --runtime <frozen-judge-runtime> --key-file <local-key-file> --arm control
```

This targeted panel consists of three previously diagnosed failures (Bronx childcare, recycled cobalt and Bronx hiring), one true-positive DART case and two contrasting controls (Bronx policing funding, Elizabeth line opening). It probes label and narrative consistency on specific known cases. It does not measure retrieval gains, incidence in the broader population or transfer to unseen commitments. Judge quality is reported separately from exact agreement with each archived product label. In particular, a judge can credit a cautious Elizabeth line label while the user still loses the distinction between eventual opening and meeting the deadline.
