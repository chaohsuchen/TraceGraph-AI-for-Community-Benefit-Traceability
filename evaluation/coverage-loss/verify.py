"""Verify every coverage variant and regenerate the integrity receipt without an API call."""
import argparse
from collections import Counter
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

from loss_audit import HERE, PROBE, put, read


def signed_test(deltas):
    values = [d for d in deltas if d]
    threshold = abs(sum(values))
    return sum(abs(sum(sign * n for sign, n in zip(signs, values))) >= threshold
               for signs in product((-1, 1), repeat=len(values))) / 2 ** len(values)


def audit(prior, runtime):
    sys.path.insert(0, str(runtime))
    sys.path.insert(0, str(prior.parent / "golden22"))
    from tracegraph import nli_eval, nli_judge
    from summarize import usage

    root = HERE / "results"
    refs = prior / "runs/candidate"
    cohort = read(prior.parent / "golden22/cohort.json")["cases"]
    families = {c["case_id"]: c.get("duplicate_of") or c["case_id"] for c in cohort}
    assert len(families) == 22
    assert read(prior / "input-audit.json")["rubric_sha256"] == nli_judge.rubric_digest()
    ledger = read(root / "loss-ledger.json")
    by_fact = {(r["case"], r["id"]): r for r in ledger}
    assert len(by_fact) == len(ledger) == 176
    decisions = [r for c in families for r in read(root / "sensitivity" / (c+".json"))["decisions"]
                 if r["label"] == "ENTAILMENT"]
    reversed_misses = [[c,r["gold_id"]] for c in families
                       for r in read(root / "sensitivity" / (c+".json"))["decisions"]
                       if r["label"] == "ENTAILMENT" and by_fact[c,r["gold_id"]]["stage"].startswith("reference_url")]
    fees = {}
    def digest(path):
        # Git may check out text artifacts with CRLF on Windows.
        return sha256(path.read_text(encoding="utf8").encode("utf8")).hexdigest()
    verified, same_outputs, quotes, flips = [], 0, 0, {}
    costs = {}
    for arm, expected in (("hidden", set(PROBE)), ("reread", set(PROBE)), ("discover", set(families))):
        paths = {s.parent.name:s.parent for s in (root/arm).glob("TG-*/score.json")}
        assert set(paths) == expected
        pcalls, jcalls = [], []
        for cid, folder in sorted(paths.items()):
            old = refs/cid
            ref, before, _ = nli_eval._inputs(old)
            after = read(folder/"research.json")
            assert ref == read(folder/"reference.json") and after["query"] == before["query"]
            frozen = read(folder/"production.json")
            pcalls.extend(frozen["calls"])
            for item in frozen["additions"]:
                assert any(w["url"] == item["url"] and item["quote"] in w["excerpt"]
                           and w.get("start",0) + w["excerpt"].index(item["quote"]) == item["start"]
                           and item["end"] - item["start"] == len(item["quote"]) for w in frozen["shortlist"]), cid
                quotes += 1
            assert before["outcome"] == after["outcome"]
            if before.get("graph"):
                for name in ("assessment","commitment"):
                    assert before["graph"][name] == after["graph"][name]
                old_f = before["graph"]["findings"]
                assert old_f == after["graph"]["findings"][:len(old_f)]
            else:
                old_f = before["job_result"].get("facts",[])
                assert old_f == after["job_result"].get("facts",[])[:len(old_f)]
            folder_with_inputs = folder
            judgment = folder/"judgment.json"
            if not judgment.is_file():
                assert nli_eval._digest(before) == nli_eval._digest(after) == read(folder/"score-reuse.json")["original_sha256"]
                judgment = old/"judgment.json"
                assert nli_eval._digest(read(judgment)) == read(folder/"score-reuse.json")["original_judgment_sha256"]
                folder_with_inputs = old
                same_outputs += 1
            else:
                jcalls.extend(read(judgment)["calls"])
            with TemporaryDirectory(prefix="tracegraph-coverage-audit-") as temp:
                work = Path(temp)
                shutil.copyfile(judgment, work/"judgment.json")
                nli_eval._score(folder_with_inputs, work)
                assert read(work/"score.json") == read(folder/"score.json")
                assert (work/"report.md").read_text(encoding="utf8") == (folder/"report.md").read_text(encoding="utf8")
            verified.append({"arm": arm, "case": cid, "score_sha256":digest(folder/"score.json"),
                             "research_sha256":digest(folder/"research.json"), "report_sha256":digest(folder/"report.md")})
        assert not any(c.get("web_search_ids") or c.get("search_enabled") for c in jcalls)
        costs[arm]={"production":dict(usage(pcalls)), "judge":dict(usage(jcalls))}
    for component in ("capture","sensitivity","reviews"):
        events=[e for f in (root/component).rglob("*.json") for e in read(f).get("calls",[])]
        assert not any(e.get("web_search_ids") or e.get("search_enabled") for e in events)
        fees[component]=dict(usage(events))
    for panel in ("probe","heldout","all22"):
        by_family = Counter()
        for row in read(root/(panel+"-pairs.json")):
            if row["arm"] == "discover":
                by_family[families[row["case"]]] += row["hits"]-row["baseline"]
        flips[panel]={"families":len(by_family),"paired_family_permutation_p":signed_test(by_family.values())}
    support_records = {}
    for folder in (root/"reviews").glob("*/*.json"):
        record = read(folder)
        support_records[folder.parent.name+"/"+folder.stem] = [record["original_identity"],record["repeated_identity"]]
    file_hashes={p.relative_to(HERE).as_posix():digest(p) for p in sorted(HERE.rglob("*"))
                 if p.is_file() and p.suffix in (".py",".json",".csv",".md") and p != HERE/"integrity.json"
                 and "__pycache__" not in p.parts}
    return {"validated_variants":len(verified),"scores_and_reports_recomputed":len(verified),
            "identical_research_reuses":same_outputs,"source_owned_additions":quotes,
            "losses":dict(Counter(r["stage"] for r in ledger)),
            "reversed_capture_entailed_misses":reversed_misses,
            "reversed_capture_entailments_including_previously_credited":len(decisions),
            "duplicate_identity_reads":support_records, "family_sensitivity":flips,
            "costs":costs,"diagnostic_costs":fees,
            "total_new_metered_usd":sum(s["estimated_usd"] for g in costs.values() for s in g.values())
                                    + sum(x["estimated_usd"] for x in fees.values()),
            "cases":verified,"hash_format":"SHA-256 of UTF-8 text with normalized LF","artifact_sha256":file_hashes}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prior", type=Path, required=True)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    result = audit(args.prior.resolve(), args.runtime.resolve())
    if args.write:
        put(HERE/"integrity.json", result)
    else:
        assert read(HERE/"integrity.json") == result
    print("Offline validation passed:", result["validated_variants"], "scores and reports;",
          result["source_owned_additions"], "exact additions; estimated USD", round(result["total_new_metered_usd"],6))

