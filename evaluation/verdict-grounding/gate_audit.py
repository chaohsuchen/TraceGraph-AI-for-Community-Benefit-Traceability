"""Check the post-v3 accepted-citation gate against every sealed v3 reader decision."""
from pathlib import Path
from replay import ROOT, CASES, TRANSFER, REPEATS, read, write


def main():
    report = {"cases": 0, "assessed_rows": 0, "independent_reader_rows": 0, "statuses_changed": [],
              "phrasing_changed": [], "internal_element_links_filtered": 0, "internal_timing_links_filtered": 0}
    for arm, cohort in (("repaired-v3", CASES), ("transfer", TRANSFER), ("repeat-2", REPEATS), ("repeat-3", REPEATS)):
        for case in cohort:
            saved = read(ROOT / "runs" / arm / case / "assessment.json")
            assert saved["status"] == "COMPLETED"
            report["cases"] += 1
            for row in saved["trace"]["criteria"]:
                report["assessed_rows"] += 1
                if not (check := row.get("entailment_check")):
                    continue
                report["independent_reader_rows"] += 1
                assert check["result"] in ("ENTAILED", "PARTIAL", "INSUFFICIENT")
                accepted = set(row["delivery_citation_ids"])
                owners = row["element_support"].values()
                timings = row["timing_support"]
                report["internal_element_links_filtered"] += sum(len(set(ids)-accepted) for ids in owners)
                report["internal_timing_links_filtered"] += sum(p["citation_id"] not in accepted for p in timings)
                if check["result"] == "ENTAILED" and row["delivery_status"] in ("PARTIAL", "DELIVERED"):
                    elements_covered = all(accepted.intersection(ids) for ids in owners)
                    timing_covered = not row["timing_requirement"] or any(p["citation_id"] in accepted for p in timings)
                    full = not check["missing"].strip() and elements_covered and timing_covered
                    expected = "DELIVERED" if full else "PARTIAL"
                    if row["delivery_status"] != expected:
                        report["statuses_changed"].append([arm,case,row["criterion"],expected])
                    if not full:
                        revised = " ".join(f'“{p["quote"]}”' for p in row["delivery_support"] if p["citation_id"] in accepted)
                        if revised != row["fulfilled_part"]:
                            report["phrasing_changed"].append([arm,case,row["criterion"]])
    write(ROOT / "post-v3-gate-audit.json", report)
    print(report)


if __name__ == "__main__":
    main()
