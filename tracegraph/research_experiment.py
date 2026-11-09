"""Compare TraceGraph research policies with isolated catalogs and resumable reports."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
from time import monotonic
from uuid import uuid4

from tracegraph.runtime import LOCAL_DSN, ROOT, create_application, evaluation_catalog, read_credential


MODEL = "claude-sonnet-4-6"
DEADLINE_SECONDS = 300
VARIANTS = ("control", "prompt", "langgraph")
QUESTIONS = {
    "H009": "What did the Bally's Bronx casino proposal promise about 70 percent of construction and facility jobs going to Bronx residents?",
    "H011": "What did Bally's Bronx casino project promise about pedestrian and bicycle access to Ferry Point Park and the waterfront, ferry shuttle service, and new bus stops?",
    "H012": "What did Bally's Bronx casino proposal promise about paying for Upwards childcare memberships that give employees priority placement?",
    "H016": "What were Bally's Bronx casino construction workforce targets for women, minorities, and service-disabled veterans?",
}


def _schedule(cases, variants):
    for index, case in enumerate(QUESTIONS):
        if case in cases:
            offset = index % len(VARIANTS)
            for variant in VARIANTS[offset:] + VARIANTS[:offset]:
                if variant in variants:
                    yield case, variant


def _revision(root):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_variant(variant, root):
    root = root.resolve()
    module_name = f"tracegraph.experimental_{variant}"
    path = root / "tracegraph" / f"experimental_{variant}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ValueError(f"Could not locate the {variant} research module.")
    source = path.read_bytes()
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
        if not all(callable(getattr(module, name, None)) for name in ("search", "evaluate_capture")):
            raise ValueError(f"The {variant} module must expose search and evaluate_capture.")
    except BaseException:
        if previous is None:
            del sys.modules[module_name]
        else:
            sys.modules[module_name] = previous
        raise
    return module, {"revision": _revision(root), "sha256": hashlib.sha256(source).hexdigest()}


def _write_checkpoint(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    stream = temporary_path.open("x", encoding="utf-8")
    try:
        with stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_report(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1 or not isinstance(report.get("runs"), list):
        raise ValueError("Expected a TraceGraph research experiment report.")
    return report


def _replay_captures(report):
    """Rebuild the same source-only capture for every replay policy."""
    if report.get("mode") != "live" or report.get("model") != MODEL:
        raise ValueError("Replay requires a live experiment report for the pinned model.")
    captures = {}
    for run in report["runs"]:
        case = run["case"]
        if case not in QUESTIONS or run["variant"] != "control" or run["query"] != QUESTIONS[case]:
            continue
        for event in reversed(run["events"]):
            if event.get("type") != "research_capture" or not event.get("citations"):
                continue
            citations = {
                citation_id: {
                    "url": passage["url"],
                    "title": passage["title"],
                    "excerpt": passage["excerpt"],
                    "context": passage["excerpt"],
                }
                for citation_id, passage in event["citations"].items()
            }
            brief = "\n\n".join(
                f"[{citation_id}] {passage['title']}\n{passage['excerpt']}"
                for citation_id, passage in citations.items()
            )
            captures[case] = brief, citations
            break
    return captures


def _replay_strategy(module, capture):
    brief, citations = capture

    def research(researcher, request, *, emit):
        passages = {citation_id: dict(passage) for citation_id, passage in citations.items()}
        if module is None:
            return brief, passages
        return module.evaluate_capture(researcher, request, brief, passages, emit=emit)

    return research


def _run_job(record, dsn, key_file, strategy, mode, checkpoint):
    started = monotonic()
    lock = Lock()

    def emit(event):
        with lock:
            elapsed = round(monotonic() - started, 3)
            record["events"].append({**event, "elapsed_seconds": elapsed})
            if event.get("type") in {"provider_call", "research_capture", "research_extracted"}:
                checkpoint()

    try:
        record["failure_stage"] = "setup"
        with evaluation_catalog(dsn) as scoped:
            app = create_application(
                scoped, "live", key_file=key_file, model=MODEL, search_strategy=strategy,
                emit=emit, deadline=started + DEADLINE_SECONDS,
                enable_conflict_review=mode == "live",
                allow_web_search=mode == "live",
            )
            record["failure_stage"] = "lookup"
            status, _, payload = app.respond(
                "POST", "/api/v1/lookup", {"Idempotency-Key": "research-experiment"},
                json.dumps({"query": record["query"]}).encode("utf-8"),
            )
            if status != 202:
                record["lookup_status"] = status
                record["outcome"] = payload.get("outcome")
                record["error"] = payload.get("error", {}).get("code", "DISCOVERY_NOT_STARTED")
                record["status"] = "FAILED"
                return
            record["failure_stage"] = "research_job"
            if not app.process_one_job():
                record["error"] = "DISCOVERY_NOT_PROCESSED"
                record["status"] = "FAILED"
                return
            job = app.store.get_job(payload["job_id"])
            record["job_status"] = job["status"]
            if job["status"] != "COMPLETED":
                record["error"] = (job.get("error") or {}).get("code", "RESEARCH_JOB_INCOMPLETE")
                record["status"] = "FAILED"
                return
            result = job["result"]
            record["outcome"] = result["outcome"]
            if result["outcome"] == "RESOLVED":
                record["failure_stage"] = "graph"
                record["graph"] = app.store.graph(result["commitment_id"])
                if record["graph"] is None:
                    record["status"] = "FAILED"
                    record["error"] = "GRAPH_NOT_FOUND"
                    return
            record["failure_stage"] = "cleanup"
        record["status"] = "COMPLETED"
        record["failure_stage"] = None
    except Exception as exc:
        # Exceptions may contain database connection or provider credentials.
        record["error"] = type(exc).__name__
        record["status"] = "FAILED"
    finally:
        total = round(monotonic() - started, 3)
        research_start = next(
            (event["elapsed_seconds"] for event in record["events"] if event.get("type") == "research_start"), None,
        )
        capture_end = next(
            (event["elapsed_seconds"] for event in record["events"] if event.get("type") == "research_capture"), None,
        )
        record["timings"] = {
            "job_seconds": total,
            "research_seconds": round(capture_end - research_start, 3)
            if capture_end is not None and research_start is not None else None,
        }
        if record["failure_stage"] == "research_job":
            record["failure_stage"] = "discovery"
            if capture_end is not None:
                record["failure_stage"] = "after_capture"
            elif research_start is not None:
                record["failure_stage"] = "research"
        if mode == "replay" and any(
            event.get("type") == "provider_call" and event.get("search_enabled") for event in record["events"]
        ):
            record.update(status="FAILED", failure_stage="replay", error="WEB_SEARCH_ATTEMPTED")


def run_experiment(*, mode, output, dsn, key_file, roots, cases, variants, captures_path=None):
    output = output.resolve()
    if output.suffix.lower() != ".json" or (captures_path and output == captures_path.resolve()):
        raise ValueError("Choose a separate .json file for the experiment output.")
    baseline = {
        "revision": _revision(ROOT),
        "file_sha256": {
            name: hashlib.sha256((ROOT / "tracegraph" / name).read_bytes()).hexdigest()
            for name in ("providers.py", "runtime.py", "research_experiment.py")
        },
    }
    config = {
        "schema_version": 1, "mode": mode, "model": MODEL, "deadline_seconds": DEADLINE_SECONDS,
        "core": baseline,
    }
    if captures_path is not None:
        config["captures_sha256"] = hashlib.sha256(captures_path.read_bytes()).hexdigest()
    report = _read_report(output) if output.exists() else {**config, "variants": {}, "runs": []}
    if any(report.get(key) != value for key, value in config.items()):
        raise ValueError("The existing report has a different experiment configuration.")
    modules = {}
    for variant in variants:
        if variant == "control":
            module, metadata = None, baseline
        else:
            if roots[variant] is None:
                raise ValueError(f"Provide --{variant}-root to run the {variant} variant.")
            module, metadata = _load_variant(variant, roots[variant])
        if report["variants"].setdefault(variant, metadata) != metadata:
            raise ValueError(f"The existing report has a different {variant} implementation.")
        modules[variant] = module
    captures = _replay_captures(_read_report(captures_path)) if captures_path else {}

    def checkpoint():
        _write_checkpoint(output, report)

    attempted = set()
    for record in report["runs"]:
        key = record["case"], record["variant"]
        if key in attempted or record["query"] != QUESTIONS.get(record["case"]):
            raise ValueError("The existing report has duplicate attempts or changed questions.")
        attempted.add(key)
        if record["status"] == "RUNNING":
            record.update(status="INTERRUPTED", error="PREVIOUS_ATTEMPT_INTERRUPTED")
    checkpoint()
    for case, variant in _schedule(cases, variants):
        if (case, variant) in attempted:
            continue
        record = {
            "case": case, "commitment_id": f"TG-GOLD-{case}", "variant": variant, "query": QUESTIONS[case],
            "status": "RUNNING", "outcome": None, "graph": None, "failure_stage": None, "events": [],
        }
        report["runs"].append(record)
        if mode == "replay" and case not in captures:
            record.update(status="SKIPPED", error="NO_CONTROL_CAPTURE")
        else:
            module = modules[variant]
            strategy = module.search if module else None
            if mode == "replay":
                strategy = _replay_strategy(module, captures[case])
            checkpoint()
            print(f"{mode} {case} {variant} RUNNING", flush=True)
            _run_job(record, dsn, key_file, strategy, mode, checkpoint)
        checkpoint()
        print(f"{mode} {case} {variant} {record['status']}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("live", "replay"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--database-url", default=os.environ.get("TRACEGRAPH_DATABASE_URL", LOCAL_DSN))
    parser.add_argument("--prompt-root", type=Path)
    parser.add_argument("--langgraph-root", type=Path)
    parser.add_argument("--case", action="append", choices=tuple(QUESTIONS))
    parser.add_argument("--variant", action="append", choices=VARIANTS)
    parser.add_argument("--captures", type=Path)
    args = parser.parse_args(argv)
    if (args.mode == "replay") != bool(args.captures):
        parser.error("--captures is required for replay and allowed only in replay mode.")
    try:
        read_credential(args.key_file)
        run_experiment(
            mode=args.mode, output=args.output, dsn=args.database_url, key_file=args.key_file,
            roots={"prompt": args.prompt_root, "langgraph": args.langgraph_root},
            cases=set(args.case or QUESTIONS), variants=tuple(dict.fromkeys(args.variant or VARIANTS)),
            captures_path=args.captures,
        )
    except (OSError, ValueError, ImportError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
