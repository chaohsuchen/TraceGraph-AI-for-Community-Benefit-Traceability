"""Build the dev/test split: a seeded, page-level dev/test split of the gold set.

    python evaluation/splits/make_dev_test_split.py

Every (document, page) that holds gold goes wholly to dev or wholly to test, so
no page has commitments in both halves. Each page goes to dev with probability
1/2 under a seeded RNG. Seeds are tried in order from BASE_SEED, and the first
assignment that is balanced within the tolerances below is kept:

* validated positives: dev and test differ by at most 2 in total, and by at
  most 2 within each applicant family (anchor = Resorts World, heldout = other);
* negatives (rows marked 0 or blank): dev and test differ by at most 6.

No row is forced into either half.
"""
from collections import Counter, defaultdict
from json import dumps
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tracegraph.corpus import load_corpus  # noqa: E402
from tracegraph.extraction_eval import align_gold, load_gold  # noqa: E402

BASE_SEED = 20261125
POS_TOL, FAMILY_TOL, NEG_TOL = 2, 2, 6

gold = align_gold(load_gold(ROOT / "evaluation" / "Golden Dataset (5).xlsx"), load_corpus(ROOT / "docs" / "tier1"))
pages = defaultdict(list)
for g in gold:
    pages[(g["document_id"], g["page"])].append(g)
keys = sorted(pages)


def counts(dev_pages):
    c = Counter()
    for key in keys:
        half = "dev" if key in dev_pages else "test"
        for g in pages[key]:
            c[(half, "pos" if g["human_validated"] else "neg", g["family"])] += 1
    return c


def balanced(c):
    pos = lambda h, f=None: sum(v for (hh, kind, fam), v in c.items() if hh == h and kind == "pos" and f in (None, fam))
    neg = lambda h: sum(v for (hh, kind, _), v in c.items() if hh == h and kind == "neg")
    return (abs(pos("dev") - pos("test")) <= POS_TOL
            and all(abs(pos("dev", f) - pos("test", f)) <= FAMILY_TOL for f in ("anchor", "heldout"))
            and abs(neg("dev") - neg("test")) <= NEG_TOL)


for seed in range(BASE_SEED, BASE_SEED + 100000):
    rng = random.Random(seed)
    dev_pages = {key for key in keys if rng.random() < 0.5}
    c = counts(dev_pages)
    if balanced(c):
        break
else:
    raise SystemExit("no balanced split found")

assignments = {g["gold_id"]: ("dev" if (g["document_id"], g["page"]) in dev_pages else "test")
               for g in sorted(gold, key=lambda g: g["gold_id"])}
out = {"name": "dev_test_split", "unit": "page", "seed": seed, "base_seed": BASE_SEED,
       "dev_pages": [f"{d}:p{p}" for d, p in sorted(dev_pages)], "assignments": assignments}
Path(__file__).with_name("dev_test_split.json").write_text(dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"seed {seed} (tried {seed - BASE_SEED + 1}); pages dev={len(dev_pages)} test={len(keys) - len(dev_pages)}")
for key in sorted(c):
    print(key, c[key])
