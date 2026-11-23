"""Build resplit_v2: a seeded, stratified 50/50 dev/test split of the gold set.

    python evaluation/splits/make_resplit_v2.py

Strata are (validated?, family), where family is the original split's applicant
family (anchor = Resorts World, heldout = other applicants). Rows inspected
during earlier B0 tuning or error analysis are forced into dev, so the test
half holds only rows that were not looked at individually.
"""
from collections import defaultdict
from json import dumps
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tracegraph.extraction_eval import load_gold  # noqa: E402

SEED = 20261121
# Old anchor_dev rows (all inspected during v1.0/v1.1 tuning) plus the six
# validated test rows inspected in v1.1 error analysis.
FORCED_DEV = {"TG-GOLD-A001", "TG-GOLD-A006", "TG-GOLD-A016", "TG-GOLD-H023", "TG-GOLD-H024", "TG-GOLD-H032"}

gold = load_gold(ROOT / "evaluation" / "Golden Dataset (5).xlsx")
forced = FORCED_DEV | {g["gold_id"] for g in gold if g["evaluation_split"] == "anchor_dev"}
strata = defaultdict(list)
for g in sorted(gold, key=lambda g: g["gold_id"]):
    family = "heldout" if g["evaluation_split"].startswith("heldout") else "anchor"
    strata[(g["human_validated"], family)].append(g["gold_id"])

rng = random.Random(SEED)
assignments = {}
for key in sorted(strata):
    ids = strata[key]
    target = round(len(ids) / 2)
    dev = [i for i in ids if i in forced]
    rest = [i for i in ids if i not in forced]
    rng.shuffle(rest)
    dev += rest[:max(0, target - len(dev))]
    for i in ids:
        assignments[i] = "dev" if i in dev else "test"

out = {"name": "resplit_v2", "seed": SEED, "forced_dev": sorted(forced),
       "assignments": dict(sorted(assignments.items()))}
(Path(__file__).with_name("resplit_v2.json")).write_text(dumps(out, indent=2) + "\n", encoding="utf-8")
for key in sorted(strata):
    ids = strata[key]
    print(f"validated={key[0]!s:5} family={key[1]:7} n={len(ids):2} dev={sum(assignments[i] == 'dev' for i in ids):2} "
          f"test={sum(assignments[i] == 'test' for i in ids):2} forced={sum(i in forced for i in ids)}")
